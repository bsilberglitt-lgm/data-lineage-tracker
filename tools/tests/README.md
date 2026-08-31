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

`t_publish.py` is the odd one out: it drives `ensure_releases()` directly rather
than the HTTP API, so it needs no running server — only the harness database.

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
