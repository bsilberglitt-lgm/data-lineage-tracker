# Contributing

This document is for anybody changing the code — including the maintainer
returning to it after a few months, and including an AI assistant working in
the repository.

If you are deploying rather than developing, you want
[`INSTALL.md`](INSTALL.md). If you are assessing the software, you want
[`SECURITY.md`](SECURITY.md).

**`CLAUDE.md` is the long version of this file.** It is the project's
engineering journal: every rule below is recorded there alongside the bug that
produced it, and it is deliberately written at length because the reasoning is
what stops the rule being "simplified" away later. This file is the short
version — what to run and what not to break. When the two disagree, `CLAUDE.md`
is right and this file needs updating.

---

## Getting set up

You need **Docker** and a local **Python 3**. The container runs Python 3.12;
locally, Python is only used to run the checks and `selftest.py`, which are
standard library only, so no virtual environment and no `pip install` are
needed on your machine.

You do **not** need Node, and you do **not** need a local PostgreSQL — both
live in containers that the scripts start and throw away.

```bash
git clone <your fork>
cd data-lineage-tracker
cp .env.example .env     # then set BOOTSTRAP_SUPERADMIN_EMAIL and _PASSWORD
docker compose up
```

The application is at `http://localhost:8765`. Migrations run automatically at
startup, and the first run seeds a fictional school district so there is
something to look at.

Then, in a second terminal:

```bash
python3 selftest.py --email you@example.gov    # prompts for the password
```

---

## The shape of the thing

Four facts about the architecture explain most of what follows.

**The backend is Python and Tornado**, with PostgreSQL reached through `db.py`
— a thin shim that keeps `?` placeholders and row-by-name access working. It is
not an ORM, and there is no model layer; handlers write SQL.

**The frontend is one file.** `static/index.html` holds the entire interface as
a single inline `<script type="text/babel">` block, compiled in the browser.
React, ReactDOM and Babel are loaded from a CDN, **pinned by version and by
`integrity` hash** — do not un-pin either. An unpinned Babel once switched to
the automatic JSX runtime, emitted an `import` statement, and produced a blank
page with the reason buried in the browser console.

**Changing one of those three versions means recomputing its hash**, or the
browser refuses the script and you get a blank page:

```bash
curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A
```

CI compares the hashes in the page against what the CDN currently serves, so a
stale hash fails the build rather than reaching anybody — but load the app
yourself after touching those lines anyway.

**The schema is Alembic migrations**, in `alembic/versions/`. Never change a
table by hand.

**Configuration is environment variables only**, so the same container runs
locally, on Render, and on AWS or Azure.

---

## Before you commit: four checks and two test runs

### The four static checks

All four run with no network, no Docker and no database, so they work anywhere
— including in an AI assistant's sandbox, where npm and PyPI are blocked. That
is why they exist: they are the substitute for the linting and type checking
this project cannot run.

```bash
python3 tools/check_jsx.py                 # JSX structure in index.html
python3 tools/check_identifiers.py --check # names used but never declared
python3 tools/check_payloads.py --check    # fields the server accepts but the browser never sends
python3 tools/gen_field_catalog.py --check # field_catalog.py is not stale
```

Each catches a class of bug that compiles cleanly and fails at runtime:

- **`check_jsx.py`** — a syntax error in the one inline script block yields a
  blank page, not an error. This is a structural scan rather than a parser, so
  a pass is not proof; see below.
- **`check_identifiers.py`** — five narrow rules finding names that are used
  and declared nowhere. That compiles perfectly and throws on render, which
  means a blank page after sign-in. One rule is about *scope* rather than
  spelling: a component declared at column zero cannot close over another
  component's variables, so a bare name it forwards must be its own.
- **`check_payloads.py`** — the update handlers write only the keys they are
  sent, so a field the server accepts and the browser never sends is
  **silently unsaveable**: the control appears, the save returns 200, and the
  value never changes. This project has found that bug five times. **When you
  add a record type, add it to `SERVER_TUPLES` in that file** — a check that
  reports OK about a file it never read is worse than no check.
- **`gen_field_catalog.py --check`** — `field_catalog.py` is **generated** from
  `static/index.html` and must not be edited by hand. Regenerate it with the
  same command without `--check` after adding, removing or relabeling a field.

### The browser compile

```bash
./tools/check_jsx.sh      # real Babel compile, in Docker — authoritative
```

Run `check_jsx.py` after every frontend edit and this before committing.
**Neither proves the page renders** — only that it compiles. After a frontend
change, click through the part you touched.

### The geometry suites

```bash
./tools/run_geometry_tests.sh    # in Docker; no local Node needed
```

Six suites covering the pure functions behind the diagram — alignment
snapping, where a task's circle sits, where a flow's label sits, the generated
flow name, which color a task takes, and which view an API request is about.

Its `SUITES` list is **written out rather than globbed, on purpose**: a new
suite has to be added there deliberately, because a suite that silently stops
being run is worse than one that does not exist — the release checklist would
claim it passed. **Add new geometry suites to that list.**

### The release gate

```bash
docker compose up
python3 selftest.py --email you@example.gov
```

`selftest.py` is the gate: authentication, role enforcement, the governance
inheritance chain, the audit trail, and that the application's one non-root
page actually serves. **It cannot catch rendering bugs.**

The focused suites in `tools/tests/` are not the gate; each was written to
reproduce one bug before fixing it, and they are kept so it cannot come back
quietly. See `tools/tests/README.md`.

### What CI does

`.github/workflows/ci.yml` runs on every push and pull request to `main`, in
four separate jobs so a red build says which *kind* of thing broke:

| Job | Runs | Needs |
|---|---|---|
| **Static checks** | The four checks above, plus `t_migration_0010.py` | Nothing |
| **JSX compiles** | The real Babel compile | One download from unpkg |
| **CDN hashes still match** | Every pinned `integrity` hash against what unpkg serves now, and that no `src` tag lacks one | Three downloads from unpkg |
| **Geometry suites** | Every suite named in `SUITES` | Nothing |
| **Release gate** | `selftest.py` and every feature suite, **twice** | A PostgreSQL service |

Three things about it are worth knowing before you edit it:

- **It derives both lists rather than keeping its own.** The Babel version is
  read out of `static/index.html`'s own script tag, and the geometry suites out
  of the `SUITES` array in `run_geometry_tests.sh`. A copy here would be a
  second list to forget — and the failure would be a check that silently stops
  running in CI while the local script still claims it passes.
- **The feature suites are globbed, not listed** — the opposite choice from
  `SUITES`, deliberately. There, a hand-kept list makes *adding* a suite a
  decision. Here, the risk worth guarding against is a new suite that never
  runs in CI at all, so a new file runs by existing. The two exclusions are
  named in the workflow with their reasons.
- **Every feature suite runs twice.** That is the borrows-the-database rule
  enforced mechanically: a suite that only works against a database nobody has
  used reports a sign-in that never happened as a pile of broken permission
  checks, and running the set twice is what caught two further suites with that
  bug after the first was fixed.

CI is a **backstop, not a gate**, for anything committed straight to `main`: it
answers after the commit exists. Run the checks locally first.

And a green build still does not mean the page renders — see below.

---

## Rules that are not negotiable

Each of these is here because breaking it caused a bug that reached a user, and
each is recorded in `CLAUDE.md` with the full story.

### Never edit a migration that has already run

Anywhere, including on one laptop. Alembic records it as applied and will not
revisit it, so your change reaches new databases only and the environment that
already ran it diverges from the code with no warning. The symptom is a 500
complaining about a column that plainly exists in the migration file.

Always add a new migration. Make catch-up migrations idempotent
(`ADD COLUMN IF NOT EXISTS`) so they are safe on databases built either way.

This is also why a badly named table stays badly named: "subtask" in the
interface is `task_step` in the database, because the rename arrived after the
migration had run.

### Use `op.add_column`, not `op.execute`

The test harness builds its schema by translating the migrations, and it
understands `op.create_table` and `op.add_column` and nothing else. Raw DDL in
`op.execute` would be **silently absent from the harness**, and every suite
touching the new column would fail with *no such column* — which reads as a bug
in the feature. The generator now exits on raw `ALTER TABLE` rather than
dropping it, so you will find out, but use the supported form.

### Boolean-ish columns are `INTEGER` 0/1, not `BOOLEAN`

The API and the frontend both depend on it. The consequence to remember is that
**JSX renders the number `0`**, so `{record.flag && <div/>}` puts a stray "0"
on the page. Write `{!!record.flag && …}`. No check catches this; the JSX is
valid.

### The audit log is append-only

Nothing edits or deletes an audit row. Reverting a change writes a *new* event.
Bulk operations get **one summary event**, never one per row — a schema upload
touches thousands of columns.

### Permissions are enforced server-side on every request

`permissions.py`, plus `READ_ROLE` and `WRITE_ROLE` on each handler. Hiding a
control in the interface is a convenience, not a security boundary. A new
endpoint needs its own check.

### Deactivate, never delete

Accounts and contributors carry `is_active` as 0/1. The audit trail refers to
people by identifier, so deleting one leaves history pointing at a record that
no longer exists. Deactivating a contributor **never clears their
assignments** — a system that named them goes on naming them, shown as
deactivated.

### Governance survives schema re-uploads

By matching on `schema.table.column` name, in `schema_import.py`. Don't break
that.

### Explanatory text uses `Tooltip`, not the `title` attribute

The browser owns `title`'s delay and restarts it on every cursor movement, and
it is invisible to keyboard users. `Tooltip` shows on focus as well as hover,
sets `aria-describedby`, and renders through a portal so it is not clipped by
the scrolling flyout.

### Colors for system types come from `TYPE_PALETTE`

Every entry has been measured against WCAG AA and recorded in
`ACCESSIBILITY.md`. An arbitrary hex value bypasses that guarantee.

**Measure contrast, don't estimate it** — over every palette entry, not the one
you assume is worst. Three contrast claims in this project's history were wrong
on the first pass, and "the lightest fill" is not the worst case.

### SVG has no z-index

Paint order *is* stacking order: whatever is written later is drawn on top and
receives the clicks. A bug of this shape looks exactly like a dead event
handler. Check the render order before debugging the handler.

### American English, everywhere in the repository

Color, catalog, behavior, gray, center, organization, analyze, labeled; "Check"
rather than "Tick". Not only what a user sees — the help articles, the code
comments, the migration docstrings and this file, because a comment in one
dialect and a label in another is how the next person copies the wrong
spelling.

Two deliberate exemptions, because changing them would be a schema or API
change dressed as a spelling fix: `aria-labelledby`, which is an HTML
attribute, and any identifier, column or endpoint that already exists.
Quotations are left as the person wrote them.

---

## What the checks cannot see

Worth stating plainly, because a green run is persuasive. Every bug that
reached a user in this project's history was invisible to automated checking.
The recurring shapes:

- **A control that is correct but never renders.** The Add Task button was
  written correctly and did not appear, because its view was missing from the
  list that decides which views get the bar it lives in.
- **A reused CSS class carrying someone else's layout assumptions.** A checkbox
  dropped into a `form-group` gets stretched to full width by a rule written
  for text inputs.
- **An error message that names the wrong cause confidently.** A 403 from a CDN
  and a 403 from an API are the same status code and different problems; ours
  blamed the API key, and sent somebody to check the one thing that was right.
- **A comment claiming a bypass that the code does not perform.** A bypass has
  to be executable. A comment asserting one is a comment agreeing with you.
- **A test built on the author's own misunderstanding.** A geometry suite
  passed for months because its fixtures shared the production code's wrong
  assumption about a property name.
- **A panel reporting derived state without noticing the thing that changes
  it.**

So: **look at the page.** After a frontend change, click the part you touched,
including the empty case — several of these bugs are only visible when a value
is absent.

---

## Working with an AI assistant

Much of this codebase was written with one, and `CLAUDE.md` is written to be
read by one. Two things make that work:

- **The assistant's sandbox has no browser and no PostgreSQL.** That is why the
  static checks and the Node geometry suites exist in the shape they do, and
  why geometry is written as pure functions taking numbers — geometry that
  reaches into the DOM is geometry nobody can check until a human clicks on it.
- **`CLAUDE.md` carries standing reminders** that run every release without
  being asked: review the help articles, review the adopter documentation,
  keep the release checklist current in both formats. They exist because the
  releases that invalidate documentation are exactly the ones where
  documentation is not what you are thinking about.

If you add a rule, add it to `CLAUDE.md` **with the bug that produced it**. The
reasoning is the part that survives; a bare rule gets optimized away by the
next person who cannot see why it is there.

---

## Pull requests

- One concern per pull request.
- Run the four static checks, `check_jsx.sh`, the geometry suites and
  `selftest.py`. Say in the description what you ran.
- Say what you clicked, for anything touching the frontend.
- If you changed the schema, say which migration number you added.
- If you changed anything about environment variables, startup, authentication,
  secrets, or what leaves the deployment, check whether `INSTALL.md` or
  `SECURITY.md` is now wrong. **A document can be made wrong by a change that
  never touches it.**

There is one maintainer and no response-time commitment. Security problems go
to the address in `SECURITY.md` rather than into a public issue.

---

## License

MIT. By contributing you agree your contribution is licensed the same way.
