"""Derive harness DDL from the real alembic migrations, by matching parens
rather than guessing with a regex — a dropped column would make the harness
silently disagree with production."""
import os, re, sys

TYPES = {"Text":"TEXT","Integer":"INTEGER","BigInteger":"INTEGER","Float":"REAL",
         "LargeBinary":"BLOB","Boolean":"INTEGER","DateTime":"TEXT"}

def blocks(src, fn_name):
    """Yield the argument text of every src call to fn_name(...)."""
    out, i = [], 0
    while True:
        i = src.find(fn_name + "(", i)
        if i < 0: return out
        j = i + len(fn_name) + 1
        depth, start = 1, j
        while depth:
            if src[j] == "(": depth += 1
            elif src[j] == ")": depth -= 1
            j += 1
        out.append(src[start:j-1]); i = j

def columns(body, collect_pk=None):
    """SQLite column fragments. `collect_pk` gathers primary-key column names.

    SQLite allows only one inline PRIMARY KEY per table, so a composite key has
    to be emitted as a table-level constraint instead. When `collect_pk` is
    given, primary-key columns are recorded there and left un-marked, and the
    caller adds the constraint. Without this, a second primary_key=True column
    produced "table has more than one primary key" and the whole harness
    database failed to build — which happened for scenario_member (patched by
    hand at the time) and again for field_definition.
    """
    cols = []
    for arg in blocks(body, "sa.Column"):
        m = re.match(r'\s*"(\w+)"\s*,\s*sa\.(\w+)\(', arg)
        if not m: continue
        name, t = m.group(1), TYPES.get(m.group(2), "TEXT")
        frag = f"{name} {t}"
        if "primary_key=True" in arg:
            if collect_pk is None:
                frag += " PRIMARY KEY"
                if "autoincrement=True" in arg and t == "INTEGER": frag += " AUTOINCREMENT"
            else:
                collect_pk.append((name, t, "autoincrement=True" in arg))
        if "nullable=False" in arg: frag += " NOT NULL"
        sd = re.search(r'server_default=sa\.text\(\s*"([^"]+)"\s*\)', arg)
        plain = re.search(r'server_default="([^"]*)"', arg)
        if sd:
            v = sd.group(1)
            frag += " DEFAULT " + ("''" if "now()" in v else v)
        elif "server_default=NOW_TEXT" in arg:
            frag += " DEFAULT ''"
        elif plain:
            # A bare string default, as in server_default="0". Only sa.text()
            # was recognised, so these were dropped silently: harmless in a
            # CREATE TABLE, where SQLite allows NOT NULL without a default,
            # but fatal in an ALTER TABLE ADD COLUMN — which is how migration
            # 0013 broke the whole harness schema with "Cannot add a NOT NULL
            # column with default value NULL". Quoted unless it is a number,
            # so a text default survives too.
            v = plain.group(1)
            frag += " DEFAULT " + (v if re.fullmatch(r"-?\d+(\.\d+)?", v)
                                   else "'" + v.replace("'", "''") + "'")
        cols.append(frag)
    return cols

# ---------------------------------------------------------------------------
# Nullability changes, in a first pass.
#
# SQLite cannot alter a column, so `op.alter_column(..., nullable=True)` has no
# SQLite equivalent and used to be dropped in silence: the harness kept the
# NOT NULL that the original CREATE TABLE declared, and every insert of a null
# failed in the test harness and nowhere else. Migration 0019 found it.
#
# So: read every migration first, note which columns stop being NOT NULL, then
# emit each CREATE TABLE without that constraint. The harness gets the schema
# the migrations describe rather than the schema the first one described.
#
# Only nullability is handled. A type change or a rename would still be lost,
# which is why the guard at the bottom fails on anything else.
# The hand-written half of the harness schema, read so the rename guard below
# can tell a rename that has been dealt with from one that has been forgotten.
SETUP_SOURCE = ""
_setup = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "harness_setup.py")
if os.path.exists(_setup):
    SETUP_SOURCE = open(_setup).read()

# **Every migration in the directory, not just the ones in argv.**
# harness_setup.py runs this script once per file, so a pass over argv alone
# would be a pass over one file — and the migration that relaxes a column is
# almost never the one that created it. Reading the whole directory is what
# makes "0016 created it NOT NULL, 0019 relaxed it" come out right no matter
# which file this invocation happens to be emitting.
RELAXED = set()
_dirs = {os.path.dirname(os.path.abspath(f)) for f in sys.argv[1:]}
for _d in _dirs:
    for _name in sorted(os.listdir(_d)):
        if not re.match(r"\d{4}_.*\.py$", _name):
            continue
        _src = open(os.path.join(_d, _name)).read()
        if "def upgrade():" not in _src or "def downgrade():" not in _src:
            continue
        _up = _src[_src.index("def upgrade():"):_src.index("def downgrade():")]
        for arg in blocks(_up, "op.alter_column"):
            m = re.match(r'\s*"(\w+)"\s*,\s*"(\w+)"', arg)
            if m and "nullable=True" in arg:
                RELAXED.add((m.group(1), m.group(2)))


def relax(table, frags):
    """Drop NOT NULL from any column a later migration made nullable."""
    out = []
    for frag in frags:
        name = frag.split(" ", 1)[0]
        if (table, name) in RELAXED:
            frag = frag.replace(" NOT NULL", "")
            # The default goes too: 0019 drops it alongside the constraint, and
            # a default left behind would keep filling the column, which is the
            # behaviour being removed.
            frag = re.sub(r" DEFAULT (?:'[^']*'|[^ ]+)", "", frag)
        out.append(frag)
    return out


for fn in sys.argv[1:]:
    src = open(fn).read()
    up = src[src.index("def upgrade():"):src.index("def downgrade():")]
    for body in blocks(up, "op.create_table"):
        m = re.match(r'\s*"(\w+)"', body)
        if not m: continue
        pk = []
        cols = relax(m.group(1), columns(body, collect_pk=pk))
        if len(pk) == 1:
            # One key: inline it, so AUTOINCREMENT still works.
            name, t, auto = pk[0]
            cols = [c + (" PRIMARY KEY" + (" AUTOINCREMENT" if auto and t == "INTEGER" else ""))
                    if c.startswith(name + " ") else c for c in cols]
        elif len(pk) > 1:
            cols.append("PRIMARY KEY (" + ", ".join(n for n, _, _ in pk) + ")")
        print(f'x("""CREATE TABLE {m.group(1)} ({", ".join(cols)})""")')
    for arg in blocks(up, "op.add_column"):
        m = re.match(r'\s*"(\w+)"\s*,', arg)
        if not m: continue
        c = relax(m.group(1), columns(arg))
        if c: print(f'x("ALTER TABLE {m.group(1)} ADD COLUMN {c[0]}")')

    # Raw DDL inside op.execute() is NOT translated. Only create_table and
    # add_column are understood, so an `op.execute("ALTER TABLE ...")` would be
    # dropped in silence and the harness would lack the column — the suites
    # then fail with "no such column", which reads as a bug in the feature
    # rather than a gap in this generator. That has happened twice in other
    # forms (0012's missing default, 0012's missing table), so it fails loudly
    # instead. Use op.create_table / op.add_column, or teach this script.
    # Anything an alter_column does besides relaxing nullability is lost, and
    # a lost schema change leaves the harness disagreeing with production about
    # a column in silence — the failure mode this script exists to prevent.
    #
    # A **rename** is the exception, because SQLite can do those and
    # harness_setup.py's preamble already writes them out by hand: that is how
    # 0002's stakeholder -> contributor arrives. So a rename is accepted only
    # when the new name actually appears in that preamble. A future rename that
    # nobody adds there fails here instead of six suites later.
    for arg in blocks(up, "op.alter_column"):
        unhandled = []
        if "type_=" in arg:
            unhandled.append("type_=")
        if "nullable=False" in arg:
            unhandled.append("nullable=False")
        rename = re.search(r'new_column_name="(\w+)"', arg)
        if rename and rename.group(1) not in SETUP_SOURCE:
            unhandled.append(f'new_column_name="{rename.group(1)}"')
        if unhandled:
            sys.exit(f"{fn}: op.alter_column uses {', '.join(unhandled)}, which "
                     f"this generator cannot translate into SQLite \u2014 the "
                     f"harness schema would silently disagree with production. "
                     f"Handle it in tools/harness_setup.py's preamble, or extend "
                     f"tools/gen_harness_schema.py.")

    for arg in blocks(up, "op.execute"):
        # An idempotent catch-up is the one legitimate case: it repairs a
        # column onto a database whose earlier create_table ran without it, so
        # a harness built from the migrations in order already has it and
        # skipping the statement is correct. That is exactly 0006. Anything
        # else is schema this generator would lose.
        if re.search(r"\bIF\s+(NOT\s+)?EXISTS\b", arg, re.I):
            continue
        if re.search(r"\b(ALTER|CREATE)\s+TABLE\b", arg, re.I):
            sys.exit(f"{fn}: op.execute() carries DDL this generator cannot "
                     f"translate, so the harness schema would be missing it. "
                     f"Use op.create_table/op.add_column, or extend "
                     f"tools/gen_harness_schema.py.\n  {arg.strip()[:120]}")
