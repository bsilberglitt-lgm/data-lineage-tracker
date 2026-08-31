"""Replicate Ben's upgrade path: 0.4.1 exists as a draft, then the code
publishes it. Checks the flip happens, the notes match the code, published
releases are still never overwritten, and the what's-new modal will fire."""
import os, sqlite3
os.environ.setdefault("DATABASE_URL", "postgresql://fake/fake")
import app
from version import VERSION

DB = os.environ["FAKE_SQLITE"]

def rows():
    c = sqlite3.connect(DB)
    r = dict(c.execute("SELECT is_published, title, notes FROM app_release "
                       "WHERE version=?", (VERSION,)).fetchone() and
             zip(["is_published","title","notes"],
                 c.execute("SELECT is_published, title, notes FROM app_release "
                           "WHERE version=?", (VERSION,)).fetchone()))
    c.close(); return r

fails = []
def ck(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond: fails.append(label)

# --- start from a draft, as Ben's database is right now -----------------
c = sqlite3.connect(DB)
c.execute("DELETE FROM app_release WHERE version=?", (VERSION,))
c.execute("""INSERT INTO app_release (version,title,notes,released_date,
             is_published,sort_key,created_date,updated_date)
             VALUES (?,?,?,?,0,700,'2026-08-30','2026-08-30')""",
          (VERSION, "Old draft title", "old body", "2026-08-30"))
# a user who has seen 0.4.0 but not 0.4.1
c.execute("UPDATE app_user SET last_seen_release='0.4.0'")
c.commit(); c.close()

print("draft -> published")
conn = app.get_db(); app.ensure_releases(conn); conn.close()
r = rows()
ck("is_published flipped to 1", r["is_published"] == 1)
ck("title re-seeded from the code", r["title"] == "Diagram controls and fixes")
ck("notes re-seeded from the code", "grab handle" in r["notes"])
ck("stale draft body is gone", "old body" not in r["notes"])

print("published releases are not overwritten")
c = sqlite3.connect(DB)
c.execute("UPDATE app_release SET notes='an administrator edited this' "
          "WHERE version=?", (VERSION,))
c.commit(); c.close()
conn = app.get_db(); app.ensure_releases(conn); conn.close()
ck("administrator edit survives a restart",
   rows()["notes"] == "an administrator edited this")

print("what's-new")
c = sqlite3.connect(DB)
seen = [x[0] for x in c.execute("SELECT last_seen_release FROM app_user").fetchall()]
latest = c.execute("SELECT version FROM app_release WHERE is_published=1 "
                   "ORDER BY sort_key DESC LIMIT 1").fetchone()[0]
nulls = c.execute("SELECT COUNT(*) FROM app_user WHERE last_seen_release IS NULL"
                  ).fetchone()[0]
c.close()
ck("0.4.1 is the newest published release", latest == VERSION)
ck("existing users still on 0.4.0, so the modal will show",
   all(s == "0.4.0" for s in seen))
ck("no user left with a NULL marker", nulls == 0)

print(("\nFAILURES: " + ", ".join(fails)) if fails else "\nall checks passed")
