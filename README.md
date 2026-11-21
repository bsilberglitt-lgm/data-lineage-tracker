# Data Lineage Tracker

A web application for documenting how data moves through an organization — which
systems hold it, what flows between them, what the fields mean, who is
accountable, and what work the pipelines depend on.

Built for the **Maine Department of Education** and shared in the hope that it
is useful to other state agencies and school districts with the same problem.
MIT licensed.

**Status: beta (0.9.x).** In production use at one agency. The schema is
stable and migrations run automatically, but the version stays on `0.x` because
the feature set is still moving.

---

## The question it answers

Most organizations can already say what systems they have and who runs them.
The question that is hard to answer quickly is the one that actually comes up:

> This field is wrong in a public report. Where did it come from, what touched
> it on the way, and who do I talk to?

This holds the answer as a diagram plus the records behind it. It is
documentation your organization maintains deliberately — it does **not** connect
to your databases, hold your actual data, or discover anything automatically.

---

## What it does

**Lineage diagram.** Systems as boxes, data flows as arrows. Drag to rearrange
with alignment guides, route arrows by their connection points and bends, zoom
and pan, and export as SVG, PNG or JPG — or print to PDF. Positions are saved.

**Catalogs.** Systems, data flows, and a data elements glossary with PII and
FERPA classification. Click any row for the full record.

**Governance inheritance.** A data element takes its owner and steward from the
system or flow that carries it unless it overrides them, so accountability is
recorded once. Every value shows whether it is inherited or overridden.

**Database schema drill-down.** Upload a T-SQL schema script against a data
store and browse its entity-relationship diagram, then govern at the table and
column level — each value inheriting from the table, then the system. Governance
already entered survives a re-upload by name, and the previous schema is kept
and can be restored.

**Tasks.** The work a flow or a store depends on: validation steps, a stored
procedure, a file somebody moves by hand. Tasks carry subtasks, dates,
assignees, dependencies on other tasks, and an automated flag — and appear on
the diagram as circles on their parent, colored by whether anything needs
attention. A task's window is derived from its subtasks unless you override it.

**Scenario planning.** A private working copy of the whole picture. Retire a
system, add three that do not exist yet, repoint flows, and compare against what
you run today — with proposed, modified and retired marked, and nothing touching
the live record until you decide. See [`SCENARIOS.md`](SCENARIOS.md).

**Resources.** Attach documents or links to systems, flows and tasks. PDFs,
images and text preview in the app; previous versions are kept and every change
is audited.

**Accounts and audit.** Four roles enforced server-side on every request. Every
change recorded field by field with old and new values, who, when and from
where — append-only, filterable, exportable to CSV, and a SuperAdmin can revert
an individual update, which writes a new entry rather than erasing one.

**Make it your own.** Your logo in one of four positions, your own definitions
for any field, fields you do not use switched off per record type (never
deleting the stored value), your own system types and colors, and a built-in
help panel with fourteen articles.

**Self-service password reset**, by SMTP or Resend, with the credential read
from the environment or a mounted secret file. Off until both the setting and a
working transport exist.

---

## Quick start

Docker is the only requirement.

```bash
git clone https://github.com/bsilberglitt-lgm/data-lineage-tracker.git
cd data-lineage-tracker
cp .env.example .env          # set BOOTSTRAP_SUPERADMIN_EMAIL and _PASSWORD
docker compose up
```

Open **http://localhost:8765** and sign in. Migrations run on startup, and an
empty database is seeded with a small **fictional** school district so there is
something to look at. Everything in it is invented; delete it once you have
seen what the features do.

```bash
python3 selftest.py --email you@example.gov   # verify, prompts for the password
```

For a real deployment — environment variables, reverse proxies, email, backups,
upgrading — read [`INSTALL.md`](INSTALL.md).

---

## Running this at another agency

You host it yourself. There is no hosted service and no account to create: you
clone the code, deploy it on your own infrastructure, and it is your database.
Nothing reports back here.

**To hear about new versions**, use the **Watch** button above → **Custom** →
**Releases**. Every release is tagged and published with notes written for
whoever has to deploy it — whether it contains a database migration, whether
it adds an environment variable, and anything a fork needs to know. Watching
*everything* instead will also mail you every issue and commit, which is
probably not what you want.

Upgrading is `git pull`, rebuild, restart; migrations run on the next start.

**If you modify the code**, fork it and track this repository as `upstream` so
you can still take fixes — [`CONTRIBUTING.md`](CONTRIBUTING.md) has the
commands. Changes that would help other agencies are welcome back as pull
requests, and anything specific to your state is yours to keep in your fork.

There is no obligation to tell anyone you are using this. But if you deploy it,
the maintainer would like to hear — what breaks for you is the fastest way to
find out what the documentation assumes.

---

## Documentation

| Document | For |
|---|---|
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | Your first hour as a user |
| [`INSTALL.md`](INSTALL.md) | Deploying and running it |
| [`SECURITY.md`](SECURITY.md) | What it does, where it stops, how to report a problem |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Changing the code |
| [`SCENARIOS.md`](SCENARIOS.md) | How scenario planning is designed |
| [`ACCESSIBILITY.md`](ACCESSIBILITY.md) | WCAG contrast measurements and known gaps |
| [`CLAUDE.md`](CLAUDE.md) | The engineering journal — every rule, with the bug that produced it |
| [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) | How a release is done |
| [`backup/README.md`](backup/README.md) | The nightly backup job and the restore procedure |

There is also a help panel **inside the application**, on every screen, which
is the manual for using it.

---

## How it is built

- **Backend:** Python and Tornado, PostgreSQL through a thin shim in `db.py`.
  No ORM; handlers write SQL.
- **Frontend:** one file, `static/index.html` — React compiled in the browser,
  with React and Babel pinned by version and verified by Subresource Integrity
  hash.
- **Schema:** Alembic migrations, applied automatically at startup.
- **Configuration:** environment variables only, so the same container runs
  locally, on Render, and on AWS or Azure.
- **Dependencies:** five Python packages, pinned to exact versions. The mail
  module is standard library only, so adding email costs nothing to audit.

```
app.py            Backend: API handlers, seeding, startup
db.py             Connection pool and the ? placeholder shim
auth.py           Password hashing and policy, sessions, lockout, reset tokens
permissions.py    Role hierarchy and permission checks
audit.py          Append-only audit trail
scenarios.py      Scenario scoping and copy-on-write
schema_import.py  T-SQL parsing and carrying governance forward
mailer.py         SMTP and Resend transports (standard library only)
help_articles.py  The in-app help panel's content
field_catalog.py  Generated — do not edit; see CONTRIBUTING.md
static/index.html The whole frontend
alembic/versions/ Schema migrations
tools/            Static checks, the test harness, and the test suites
backup/           Nightly database backup job for S3
selftest.py       End-to-end verification
```

---

## Not yet built

Listed because knowing what is missing is part of deciding whether to adopt it.

- **Two-factor authentication.** The preference exists and is locked off; TOTP
  is the intended implementation.
- **Single sign-on.** No OIDC or SAML — everyone has a local password. For an
  organization with an identity provider this is the most likely objection.
- **Email notifications for tasks** — assignment and overdue. The mailer is
  built; the open question is the shape, and a scheduled digest is probably the
  better first version.
- **Field-level lineage tracing** — following a single column back through the
  transformations that produced it. The field panel has a placeholder for it,
  pending column-level source mappings.
- **An agreements screen.** The table and API exist; there is no interface.

`SECURITY.md` lists the security-relevant gaps, which are a different list.

---

## Contributing and support

This is maintained by one person alongside other work, so expect no
response-time commitment. Pull requests and issues are welcome;
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the rules, and security problems should
go to the address in [`SECURITY.md`](SECURITY.md) rather than into a public
issue.

If you deploy this at another agency, the maintainer would like to hear about
it — partly out of interest, and partly because what breaks for you is the
fastest way to find out what the documentation assumes.

---

## License

MIT. See [`LICENSE`](LICENSE).
