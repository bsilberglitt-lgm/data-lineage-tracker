"""Migration 0010: converting the "Deprecated" status, and recording what it was.

    python3 tools/tests/t_migration_0010.py

The harness builds its schema by extracting DDL from the migrations, so a
migration that only moves *data* is invisible to it — `reset_db.py` would run
clean whether this one worked or not. This runs the migration's actual SQL
against SQLite with a stub `op`, which is the only way to exercise it here:
there is no PostgreSQL in the assistant's sandbox, and no Alembic either.

What matters, in order:

  1. **Deprecated records are converted.** The obvious part.
  2. **The original value survives in the audit log.** This migration is
     irreversible by design, and "Under Review" does not mean the same thing as
     "Deprecated" — a point raised before it was written. The audit trail is
     therefore the only record of what each value used to be, and the thing
     that makes a mislabeled record recoverable by hand.
  3. **Records that were already "Under Review" are left alone**, and not
     swept into the audit event as though they had been changed.
  4. **It is safe to run when there is nothing to convert**, which is the real
     production case: there were zero Deprecated records.
"""
import datetime
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE)) if HERE.endswith("tests") else HERE
MIGRATION = os.path.join(os.path.dirname(HERE), "..",
                         "alembic", "versions", "0010_retire_deprecated_status.py")

PASS, FAIL = [], []


def ck(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label +
          ("" if cond or not detail else f"   [{detail}]"))


class FakeText:
    """Stands in for sqlalchemy.text: turns :name parameters into ?-style."""
    def __init__(self, sql):
        self.sql = sql

    def bind(self, params):
        names = re.findall(r":(\w+)", self.sql)
        return re.sub(r":\w+", "?", self.sql), [params[n] for n in names]


class FakeConn:
    def __init__(self, db):
        self.db = db

    def execute(self, stmt, params=None):
        sql, args = stmt.bind(params or {})
        return self.db.execute(sql, args)


def build(db, rows):
    """A database with just the tables this migration touches."""
    db.executescript("""
        CREATE TABLE system (system_id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE data_flow (flow_id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE audit_event (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT,
            actor_user_id INTEGER, actor_email TEXT, action TEXT,
            entity_type TEXT, entity_id TEXT, summary TEXT);
        CREATE TABLE audit_field_change (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER,
            field_name TEXT, old_value TEXT, new_value TEXT);
    """)
    for t, pk, items in rows:
        for ident, status in items:
            db.execute(f"INSERT INTO {t} ({pk}, status) VALUES (?,?)", (ident, status))
    db.commit()


def run_migration(db):
    """Execute the real upgrade() body against SQLite.

    The migration does `from alembic import op` and `import sqlalchemy as sa`
    at module level, so stub modules go into sys.modules before it is compiled
    — neither library is installed in the sandbox, and installing them to test
    a dozen lines of SQL would be the wrong trade.
    """
    import types
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = type("op", (), {"get_bind": staticmethod(lambda: FakeConn(db))})
    fake_sa = types.ModuleType("sqlalchemy")
    fake_sa.text = FakeText
    sys.modules["alembic"] = fake_alembic
    sys.modules["sqlalchemy"] = fake_sa

    src = open(MIGRATION).read()
    ns = {"__name__": "migration_0010"}
    exec(compile(src, MIGRATION, "exec"), ns)
    ns["upgrade"]()
    db.commit()
    return ns


print("-- records are converted, and what they were is recorded --")
db = sqlite3.connect(":memory:")
build(db, [
    ("system", "system_id", [("SYS-001", "Deprecated"), ("SYS-002", "Active"),
                             ("SYS-003", "Deprecated"), ("SYS-004", "Under Review")]),
    ("data_flow", "flow_id", [("PL-001", "Deprecated"), ("PL-002", "Active")]),
])
run_migration(db)

left = db.execute("SELECT COUNT(*) FROM system WHERE status='Deprecated'").fetchone()[0]
left += db.execute("SELECT COUNT(*) FROM data_flow WHERE status='Deprecated'").fetchone()[0]
ck("no Deprecated records remain", left == 0, str(left))

ck("the two Deprecated systems became Under Review",
   db.execute("SELECT COUNT(*) FROM system WHERE status='Under Review'").fetchone()[0] == 3,
   "expected 3 — the two converted plus the one already Under Review")
ck("the Active system was untouched",
   db.execute("SELECT status FROM system WHERE system_id='SYS-002'").fetchone()[0] == "Active")
ck("the flow was converted too",
   db.execute("SELECT status FROM data_flow WHERE flow_id='PL-001'").fetchone()[0] == "Under Review")

print("\n-- the audit trail is the only record of the original value --")
events = db.execute("SELECT event_id, entity_type, summary FROM audit_event").fetchall()
ck("one event per affected table, not one per record", len(events) == 2,
   f"{len(events)} events")
ck("the summary names the migration and the counts",
   all("0010" in e[2] and "Deprecated" in e[2] for e in events),
   str([e[2][:60] for e in events]))

changes = db.execute(
    "SELECT field_name, old_value, new_value FROM audit_field_change "
    "ORDER BY field_name").fetchall()
ck("one field change per converted record", len(changes) == 3, str(len(changes)))
ck("each names the record it refers to",
   sorted(c[0] for c in changes) == ["PL-001.status", "SYS-001.status", "SYS-003.status"],
   str([c[0] for c in changes]))
ck("each preserves the original value", all(c[1] == "Deprecated" for c in changes))
ck("and the value it became", all(c[2] == "Under Review" for c in changes))
ck("the record that was already Under Review is not in the audit trail",
   "SYS-004.status" not in [c[0] for c in changes],
   "it was never changed, so claiming otherwise would be false")

print("\n-- the production case: nothing to convert --")
db2 = sqlite3.connect(":memory:")
build(db2, [("system", "system_id", [("SYS-001", "Active")]),
            ("data_flow", "flow_id", [("PL-001", "Active")])])
try:
    run_migration(db2)
    ck("runs cleanly with no Deprecated records", True)
except Exception as e:
    ck("runs cleanly with no Deprecated records", False, repr(e))
ck("and writes no audit noise",
   db2.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0] == 0,
   "an empty migration should leave no trace")

print("\n-- running twice is harmless --")
run_migration(db)
ck("a second run converts nothing and adds no events",
   db.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0] == 2,
   "migrations should not be destructive if replayed")

print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
