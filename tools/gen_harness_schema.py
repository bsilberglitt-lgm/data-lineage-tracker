"""Derive harness DDL from the real alembic migrations, by matching parens
rather than guessing with a regex — a dropped column would make the harness
silently disagree with production."""
import re, sys

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

def columns(body):
    cols = []
    for arg in blocks(body, "sa.Column"):
        m = re.match(r'\s*"(\w+)"\s*,\s*sa\.(\w+)\(', arg)
        if not m: continue
        name, t = m.group(1), TYPES.get(m.group(2), "TEXT")
        frag = f"{name} {t}"
        if "primary_key=True" in arg:
            frag += " PRIMARY KEY"
            if "autoincrement=True" in arg and t == "INTEGER": frag += " AUTOINCREMENT"
        if "nullable=False" in arg: frag += " NOT NULL"
        sd = re.search(r'server_default=sa\.text\(\s*"([^"]+)"\s*\)', arg)
        if sd:
            v = sd.group(1)
            frag += " DEFAULT " + ("''" if "now()" in v else v)
        elif "server_default=NOW_TEXT" in arg:
            frag += " DEFAULT ''"
        cols.append(frag)
    return cols

for fn in sys.argv[1:]:
    src = open(fn).read()
    up = src[src.index("def upgrade():"):src.index("def downgrade():")]
    for body in blocks(up, "op.create_table"):
        m = re.match(r'\s*"(\w+)"', body)
        if not m: continue
        print(f'x("""CREATE TABLE {m.group(1)} ({", ".join(columns(body))})""")')
    for arg in blocks(up, "op.add_column"):
        m = re.match(r'\s*"(\w+)"\s*,', arg)
        if not m: continue
        c = columns(arg)
        if c: print(f'x("ALTER TABLE {m.group(1)} ADD COLUMN {c[0]}")')
