"""Replicate Ben's upgrade path: the current version exists in the database as
a stale draft, then the code publishes it. Checks the flip happens, the wording
is re-seeded from the code, published releases are still never overwritten, and
the what's-new marker is left so the modal fires.

Everything version-specific is read out of ``SEED_RELEASES`` rather than typed
in here, so this keeps working at 0.5.0 and beyond. Hardcoding one release's
title made it fail the moment the next one landed, which is noise, not a bug.
"""
import os, sqlite3
os.environ.setdefault("DATABASE_URL", "postgresql://fake/fake")
import app
from version import VERSION

DB = os.environ["FAKE_SQLITE"]

# What the code says this release should look like once published.
SEEDED = next(r for r in app.SEED_RELEASES if r[0] == VERSION)
SEED_TITLE, SEED_NOTES = SEEDED[1], SEEDED[5]
# The release published immediately before this one — what a user who is up to
# date but has not seen this release would be marked as having seen.
PREVIOUS = [r[0] for r in app.SEED_RELEASES if r[0] != VERSION][-1]

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
# a user who is up to date apart from this release
c.execute("UPDATE app_user SET last_seen_release=?", (PREVIOUS,))
c.commit(); c.close()

print("draft -> published")
conn = app.get_db(); app.ensure_releases(conn); conn.close()
r = rows()
ck("is_published flipped to 1", r["is_published"] == 1)
ck("title re-seeded from the code", r["title"] == SEED_TITLE)
ck("notes re-seeded from the code", r["notes"] == SEED_NOTES)
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
ck(f"{VERSION} is the newest published release", latest == VERSION)
ck(f"existing users still on {PREVIOUS}, so the modal will show",
   all(s == PREVIOUS for s in seen))
ck("no user left with a NULL marker", nulls == 0)

print(("\nFAILURES: " + ", ".join(fails)) if fails else "\nall checks passed")
