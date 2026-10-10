# Development test suites

Focused suites used while building a release, run against the sandbox harness
(see `tools/harness_setup.py`). They are **not** the release gate — that is
`selftest.py`, which runs against a real PostgreSQL instance and is the one the
release checklist requires.

These exist because each was written to reproduce a specific bug before fixing
it, and they are kept so the same bug cannot come back quietly:

| Suite | What it pins down |
|---|---|
| `t_041.py` | Manual Entry inside a scenario, uploads carrying the scenario, required fields on data flows |
| `t_branch.py` | Branching a scenario preserves proposed / modified / retired; the Proposed toggle |
| `t_route.py` | Connection points and bends: validation, and that routing is layout rather than content |
| `t_fieldlist.py` | The field list slot on flows, clearing without deleting, scenario isolation |
| `t_publish.py` | Publishing a release: a draft is re-seeded from the code, a published one is never overwritten, and the what's-new marker is left so the modal fires |
| `t_branding.py` | The logo: only real PNG/JPEG bytes are stored, both caps hold, the image is public but everything else is SuperAdmin, and the ETag changes when the image does |
| `t_autoname.js` | The generated data flow name follows the two systems while it is still generated, and never overwrites one the user typed |
| `t_044.py` | Retiring and un-retiring in a scenario; the Proposed box not clobbering it; copy_members with roles and overlapping invites; editing a resource's label, description and URL |
| `t_labelpos.js` | Where a flow's label sits: clear of the arrowhead on short upward flows, unchanged everywhere else |
| `t_migration_0010.py` | The Deprecated conversion, and that the original value is preserved in the audit log |
| `t_fielddefs.py` | Field definitions: one definition found under every spelling of its field, the same label on two record types kept separate, permissions, and the audit trail |
| `t_fieldhide.py` | Field hiding: a required field cannot be switched off, the shared `*` scope is refused, the stored value survives, every label resolves to its column, permissions, and the audit trail |

Four need no running server. `t_publish.py` and `t_migration_0010.py` drive the
code directly rather than the HTTP API; the two JavaScript suites read geometry
and naming rules straight out of `index.html` and replay them in Node, which is
the only way to exercise browser-only logic here.

`t_migration_0010.py` is worth a note: the harness builds its schema by
extracting DDL from the migrations, so a migration that only moves *data* is
invisible to it. That suite runs the migration's real SQL against SQLite with a
stub `op`, which is as close as the sandbox gets.

    node tools/tests/t_autoname.js
    node tools/tests/t_labelpos.js
    python3 tools/tests/t_migration_0010.py

    PYTHONPATH=/tmp/h2/fakelibs FAKE_SQLITE=/tmp/h2/test.db \
      DATABASE_URL=postgresql://fake/fake python3 tools/tests/t_publish.py

Run one of the others:

    python3 tools/harness_setup.py
    cd /tmp/h2 && python3 reset_db.py
    PYTHONPATH=/tmp/h2/fakelibs FAKE_SQLITE=/tmp/h2/test.db \
      DATABASE_URL=postgresql://fake/fake PORT=8900 \
      BOOTSTRAP_SUPERADMIN_EMAIL=you@example.gov \
      BOOTSTRAP_SUPERADMIN_PASSWORD='Bootstrap-Temp-7xQ!' python3 app.py &
    python3 tools/tests/t_route.py 8900

Each expects a freshly reset database and changes the bootstrap password as its
first act, so run `reset_db.py` between suites.
