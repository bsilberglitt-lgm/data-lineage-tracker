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
| `t_align.js` | Diagram geometry in Node: alignment snapping is inert outside its tolerance, guides span both boxes, every multiple of 45 degrees snaps exactly and nothing between them moves, and the export covers the whole diagram |
| `t_060.py` | The self-service profile's boundary: name and title only, everything else refused by name, a mixed request refused whole, and the title resolving from a linked account |
| `t_052.py` | The payload fix proved by round trip using the real payload shape, contributor deactivation keeping its assignments, and the delete confirmation's counts and blockers |
| `t_fieldhide.py` | Field hiding: a required field cannot be switched off, the shared `*` scope is refused, the stored value survives, every label resolves to its column, permissions, and the audit trail |
| `t_taskpos.js` | Where a task's circle sits on a flow or inside a system, and that dragging it round-trips to the nearest point on its line |
| `t_tasks.py` | Tasks: the derived status and its two asymmetries, what a copy carries and what it drops, one parent inside the view being written, dependencies refused three ways, the date order rule, and that dragging a circle is not audited |
| `t_tasksignal.js` | Which color a task's circle takes: the precedence in full, both routes to "not started", the automated blue, and the labels read out of the source so a color change cannot pass |
| `t_email.py` | Password reset: a forged `Host` header ignored, the token in the fragment and not the query string, the same response for an address that exists and one that does not, the rate limit, and no mail setting or credential in any public response |
| `t_avatarcrop.js` | The cropper's arithmetic in Node: zoom 1 fills the stage, no offset at any zoom leaves a gap, the rectangle drawn stays inside the source image, and zooming keeps the same point under the circle. Swept over many sizes and zooms rather than asserted at one, because an off-by-one that only shows on a portrait image at high zoom would pass a single example |
| `t_avatar.py` | Profile pictures: the type taken from the bytes and not the filename, an SVG called .png refused, both caps, the existing picture surviving a refusal, the digest in the payload but never the image, a session required unlike the logo, and removal told apart from nothing-to-remove |
| `t_2fa.py` | Two-factor authentication: enrollment only completing once a code is proved, a password buying a challenge rather than a session, the attempt cap, recovery codes used once and earning no remembered device, devices revoked by hand and by a password change, forced enrollment, and an administrator's reset stopping at somebody who outranks them |
| `t_scopedurl.js` | Which view an API request is about: the scenario parameter never duplicated, and the explicit opt-out actually honored at its call site |

Several need no running server. `t_publish.py` and `t_migration_0010.py` drive
the code directly rather than the HTTP API; the JavaScript suites read geometry,
naming and scoping rules straight out of `index.html` and replay them in Node,
which is the only way to exercise browser-only logic here.

All six `.js` suites are also run together, in Docker, by
`tools/run_geometry_tests.sh` — which is what the release checklist calls. Their
`SUITES` list there is hand-kept on purpose; **a new JavaScript suite has to be
added to it**, or it silently stops being run while the checklist claims it
passed.

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

## Credentials, and what a suite may not assume

The paragraph that used to sit here said each suite "changes the bootstrap
password as its first act" and needed a freshly reset database. Both were true
and both were faults, fixed by `_client.py` — read its docstring before writing
a suite. Three rules follow from it:

- **Sign-in goes through `_client`.** Credentials come from `--email` plus a
  prompt. **Nothing changes the password of an account the suite borrows**: a
  test that rewrites a real person's credentials is a footgun, and it stayed
  harmless only because it could not get in.
- **An account with two-factor authentication is handled, and that is new in
  0.10.0.** A correct password returns a *challenge* rather than a session, so
  `_client.finish_challenge` asks for a code — `--code`, `TEST_2FA_CODE` for
  the harness, or a prompt; a recovery code works and is spent. Before it
  existed, the day somebody enrolled their own account every suite here
  reported "Could not sign in. Check the email and password", which is a
  message about the one thing that was right. **Nothing changes the second
  factor of a borrowed account either**, for the same reason as the password —
  and that extends to `require_2fa`, which is deployment-wide: `t_2fa.py` will
  not touch it unless the borrowed account is already enrolled.
- **A suite borrows the database; it does not own it.** It may not assert a
  starting state it does not control — not "nothing is hidden", not "nothing is
  defined". Assert the *relationship* the check is reaching for, and restore
  anything you change, in a `finally` so a crash cannot skip it.
  "You do not control it" includes the suite that ran before yours — **and a
  setting a person configured months ago.** `t_2fa.py`'s emailed-code section
  assumed a working mail transport, which was true of every machine it was
  written on because mail had been set up there by hand, and false in CI, which
  builds from nothing. It arranges the transport itself now, restores it, and
  skips with the reason printed when it cannot (the credential comes from the
  environment, so no suite can supply one). **If your suite needs a setting,
  set it — do not inherit it.**
- **A suite's own accounts go through `_client.ensure_account`**, which creates
  or resets them so a second run behaves like the first. Use
  `_client.unique_email` where resetting is not enough — a check about an
  account nobody has touched cannot be repeated on one the last run linked.

  Three suites once had the same second-run failure, and each reported it as
  nine permission checks failing rather than as a sign-in that never happened.

- **A section must put back any deployment-wide switch it flips, before the
  next section runs** — not only in the closing `finally`. `t_2fa.py` left
  `allow_email_2fa` on, which made the next section's subject an account that
  could be challenged by email, and ten assertions failed for a reason that
  had nothing to do with what they were about.

**Run every suite twice, in any order, before believing it.**

A missing precondition should **stop** the suite with the reason and the exact
command to fix it — not be checked with an ordinary assertion, which reports
the cause everywhere except where it is. And a restore in a `finally` has to
survive the state the crash left: reading a response as the shape it has when
things went well is how `t_2fa.py` first crashed inside its own safety net,
printing no summary at all.
