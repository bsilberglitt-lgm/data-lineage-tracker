"""Replicate Ben's upgrade path: the current version exists in the database as
a stale draft, then the code re-seeds it. Checks that the wording and the
published flag both come from ``SEED_RELEASES``, that a published release is
never overwritten, and that the what's-new marker is left so the modal fires.

**Everything version-specific is read out of ``SEED_RELEASES``, including
whether the release is published.** Two earlier versions of this file hardcoded
what the current release happened to look like, and both started failing on
correct behaviour the moment the next release landed:

* the first typed in one release's title and a phrase from its body;
* the second assumed the current version was published, which is false for the
  whole period between drafting the notes and Ben approving them — which is
  exactly when this test is most likely to be run.

A test that cries wolf during the normal workflow is worse than no test, so
this one asserts "matches the code" rather than any particular state.

**Harness-only.** Unlike the other suites this one imports `app` and reads the
SQLite file named by `FAKE_SQLITE` directly, so it runs in the assistant's
sandbox and nowhere else — the app's dependencies are not installed on a normal
machine. It is deliberately absent from the release checklist for that reason;
do not add it.
"""
import os, sqlite3
os.environ.setdefault("DATABASE_URL", "postgresql://fake/fake")
import app
from version import VERSION

DB = os.environ["FAKE_SQLITE"]

# What the code says this release should look like. Index 3 is the published
# flag, so a draft and a published release are both handled without editing.
SEEDED = next(r for r in app.SEED_RELEASES if r[0] == VERSION)
SEED_TITLE, SEED_PUBLISHED, SEED_NOTES = SEEDED[1], SEEDED[3], SEEDED[5]

# The release before this one. Used both as the what's-new marker and, when the
# current release is still a draft, as the newest *published* release.
PREVIOUS = [r[0] for r in app.SEED_RELEASES if r[0] != VERSION][-1]

# A release that is definitely published, for the "never overwritten" check.
# It cannot be the current one while that is still a draft.
PUBLISHED_VER = VERSION if SEED_PUBLISHED else PREVIOUS

STATE = "published" if SEED_PUBLISHED else "a draft awaiting review"


def row(version=None):
    c = sqlite3.connect(DB)
    got = c.execute("SELECT is_published, title, notes FROM app_release "
                    "WHERE version=?", (version or VERSION,)).fetchone()
    c.close()
    return dict(zip(["is_published", "title", "notes"], got)) if got else {}


fails = []


def ck(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


print(f"-- {VERSION} is {STATE} in the code --")

# --- start from a stale draft, as Ben's database is before a deploy ---------
c = sqlite3.connect(DB)
c.execute("DELETE FROM app_release WHERE version=?", (VERSION,))
c.execute("""INSERT INTO app_release (version,title,notes,released_date,
             is_published,sort_key,created_date,updated_date)
             VALUES (?,?,?,?,0,9999,'2026-08-30','2026-08-30')""",
          (VERSION, "Old draft title", "old body", "2026-08-30"))
c.execute("UPDATE app_user SET last_seen_release=?", (PREVIOUS,))
c.commit(); c.close()

conn = app.get_db(); app.ensure_releases(conn); conn.close()
r = row()
ck("the published flag matches the code", r["is_published"] == SEED_PUBLISHED)
ck("title re-seeded from the code", r["title"] == SEED_TITLE)
ck("notes re-seeded from the code", r["notes"] == SEED_NOTES)
ck("stale draft wording is gone", "old body" not in r["notes"])

print("\n-- a published release is never overwritten --")
c = sqlite3.connect(DB)
c.execute("UPDATE app_release SET notes='an administrator edited this' "
          "WHERE version=?", (PUBLISHED_VER,))
c.commit(); c.close()
conn = app.get_db(); app.ensure_releases(conn); conn.close()
ck(f"an administrator's edit to {PUBLISHED_VER} survives a restart",
   row(PUBLISHED_VER)["notes"] == "an administrator edited this")

print("\n-- the what's-new marker --")
c = sqlite3.connect(DB)
seen = [x[0] for x in c.execute("SELECT last_seen_release FROM app_user").fetchall()]
latest = c.execute("SELECT version FROM app_release WHERE is_published=1 "
                   "ORDER BY sort_key DESC LIMIT 1").fetchone()[0]
nulls = c.execute("SELECT COUNT(*) FROM app_user "
                  "WHERE last_seen_release IS NULL").fetchone()[0]
c.close()

if SEED_PUBLISHED:
    ck(f"{VERSION} is the newest published release", latest == VERSION)
    ck(f"users still on {PREVIOUS}, so the modal will show",
       all(s == PREVIOUS for s in seen))
else:
    # While the notes are a draft nobody should be shown them, and the newest
    # published release must still be the previous one.
    ck(f"{VERSION} is a draft, so {PREVIOUS} is still the newest published",
       latest == PREVIOUS)
    ck("no user is shown the unreviewed notes",
       all(s == PREVIOUS for s in seen))

ck("no user left with a NULL marker", nulls == 0)

print(("\nFAILURES: " + ", ".join(fails)) if fails else "\nall checks passed")
raise SystemExit(1 if fails else 0)
