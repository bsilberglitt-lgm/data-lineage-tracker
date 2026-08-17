# Data Lineage Tracker

An interactive web application for tracking data lineage across the Department of Education's ecosystem. Visualize how data flows between systems, drill into transformation details, and maintain a living catalog of systems, data flows, data elements, stakeholders, and agreements.

## Quick Start

The app runs as a container alongside a PostgreSQL database, so local development
matches production exactly.

### Prerequisites
- **Docker Desktop** ([download](https://www.docker.com/products/docker-desktop/)) — running

### Run
```bash
cd data-lineage-tracker
docker compose up
```

Then open **http://localhost:8765** in your browser.

On startup the app:
1. Waits for PostgreSQL to be ready
2. Runs Alembic migrations to create or update the schema
3. Seeds baseline data if the database is empty (systems, flows, data elements, and the NEO and Federal Reporting database schemas)
4. Serves the app on port 8765

### Everyday commands
```bash
docker compose up              # start (Ctrl+C to stop)
docker compose up -d           # start in the background
docker compose down            # stop; your data is kept
docker compose restart app     # apply code changes
docker compose logs -f app     # watch the logs
docker compose down -v         # stop and DELETE all data
```

Code changes are picked up with `docker compose restart app`. If you change
`requirements.txt`, rebuild with `docker compose up --build`.

### First sign-in
The first time the app starts against an empty database it creates a SuperAdmin
account from `BOOTSTRAP_SUPERADMIN_EMAIL` (set in `docker-compose.yml`, or your
own `.env`). If you don't supply a password, one is generated and printed to the
logs **once**:

```
====================================================================
  Initial SuperAdmin created
    Email:    you@example.gov
    Password: <generated>
    (shown once — you must change it at first login)
====================================================================
```

Sign in with it and you'll be required to choose a new password before you can
go anywhere else.

### Verify everything works
```bash
python3 selftest.py --email you@example.gov --password 'YourPassword'
```
Exercises every endpoint, the governance inheritance chain, the read/write
paths, and — importantly — checks that roles are actually enforced on the
server, by creating a temporary Viewer account and confirming it is blocked from
writing. Uses only the Python standard library.

### Configuration
All configuration is via environment variables (no code changes needed to move
hosts):

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (required) | set by Docker Compose |
| `PORT` | Port to listen on | `8765` |
| `BOOTSTRAP_SUPERADMIN_EMAIL` | First-run only: creates the initial SuperAdmin | — |
| `BOOTSTRAP_SUPERADMIN_PASSWORD` | Optional; generated and logged once if omitted | — |
| `SCRYPT_N` | Password hashing cost | `32768` |

See `.env.example`.

## Accounts, Roles, and the Audit Trail

### Roles
Roles are strictly ordered — each inherits everything below it.

| Role | Can do |
|---|---|
| **Viewer** | Read the diagram, catalogs, and schema drill-down, including all detail panels. Cannot edit anything, including rearranging the diagram. |
| **Editor** | Everything a Viewer can, plus create/update/delete systems, data flows, data elements, and contributors, and edit table- and field-level schema governance. |
| **Administrator** | Everything an Editor can, plus create and manage user accounts and assign roles (except SuperAdmin). |
| **SuperAdmin** | Everything an Administrator can, plus the Preferences and Audit Log screens, and managing other SuperAdmins. |

Permissions are enforced **on the server for every request**. The interface also
hides controls a user can't use, but that's only a convenience — the server is
the security boundary.

### Managing accounts
There is no self-registration. An Administrator creates each account with a
temporary password, which must be shared through a separate channel (email
delivery isn't configured yet) and must be changed at first sign-in.

Accounts are **deactivated, never deleted**, so audit history stays intact.
Deactivation and role changes take effect immediately, on the deactivated user's
very next request. The system refuses to demote or deactivate the last remaining
SuperAdmin.

### Sessions and passwords
Sessions live in the database, so an Administrator can see who's signed in and
force a sign-out. A session expires after 8 hours idle (configurable) and after
30 days regardless. Passwords are hashed with scrypt, must be at least 12
characters with upper case, lower case, a number and a symbol, are screened
against a list of common passwords, and can't contain the user's own name or
email. Repeated failed sign-ins trigger progressive delays and then a temporary
lockout.

### Audit log
Every create, update, and delete is recorded **field by field**, with the old and
new values, who made the change, when, and from which IP address. Sign-ins,
failed sign-ins, and permission denials are recorded too. The log is
append-only — nothing in the application can edit or remove an entry.

SuperAdmins can filter by date range, user, action, and entity type, and export
the filtered view to CSV.

**Reverting a change.** A SuperAdmin can revert an individual update from the
audit log, restoring the values as they were before it. If a field has changed
since, the app warns and names the newer values before letting you proceed. The
revert is written as a *new* audit entry — nothing is erased. Creations and
deletions are not revertible, and schema uploads revert through their own saved
versions instead.

### Not yet built
Two-factor authentication and self-service password reset both need email
delivery, which isn't configured yet. Their toggles appear in Preferences,
visibly locked, and the surrounding code is structured so they can be added
without rework. When single sign-on (OIDC against a state identity provider)
arrives, it replaces the sign-in step only — roles, permissions, and the audit
trail stay exactly as they are.

## Versioning and Releases

The current version lives in `version.py` and appears in the bottom-right corner
of every page, including the sign-in screen. Click it to open **Release Notes**.

Release notes are stored in the database. A SuperAdmin writes them (markdown:
`## heading`, `* bullet`, `**bold**`, `[link](url)`), saves them as a **draft**,
and publishes when ready. Publishing shows the notes once to each user at their
next sign-in — someone returning after several releases sees all of them in
order, oldest first, with *Remind me later* and *Got it*.

> **Releasing:** bump `version.py`, draft the notes, and get them reviewed before
> publishing. See `CLAUDE.md`.

## What You Can Do

### Lineage Diagram
- **Drag nodes** to rearrange the layout (positions are saved)
- **Click a system node** to see its details in the side panel
- **Click a flow line** (the arrows between nodes) to see transformation steps, business rules, validations, data elements carried, and more
- **Scroll to zoom** in and out
- **Click and drag the background** to pan

### Resources on systems and data flows
Attach **documents or links** with your own label. PDFs, images, and text files
preview in the app; Office documents show their details with a Download button
(browsers can't render Word or Excel without a conversion service). To change a
document, download it, edit, and upload it back — the previous version is kept
and the change is recorded in the audit log. Links open in a new tab.

Files are capped at 10 MB and stored in PostgreSQL for now, so they're covered by
the database backups. See `CLAUDE.md` for the planned move to S3.

### Manual Entry systems
The **Manual Entry** system type (formerly *Manual Process*) tracks data typed or
uploaded directly into a data store. Alongside the usual ownership fields it
records the entry type (direct entry or file upload), who does the entering,
what validates the data at entry, and how errors get corrected.

Choosing a target data store **creates the inbound data flow automatically**, so
the timing questions are only answered once; changing the target repoints that
flow. Each Manual Entry system also gets a fixed **Data entry field list**
resource slot for a sample file or list of field headers.

### Uploading a database schema
Administrators can upload a **SQL Server (T-SQL) schema script** to a Data Store
system to refresh its tables and columns. Governance already entered is carried
forward wherever the schema, table, and column names still match, so hand-entered
classifications and stewards aren't lost. The previous schema is saved and can be
restored, and the whole upload is recorded as a single audit entry rather than
thousands of per-column rows.

### Catalog Views
- **Systems** — table view of all systems with type, status, and PII flags
- **Data Flows** — table view of all pipelines with source, destination, and frequency
- **Data Elements** — glossary of all data fields with PII/FERPA classification

Click any row to open its detail panel.

### Database Schema Drill-Down
Systems that have a database schema mapped — currently **NEO** (State Operational Data Store) and **Federal Reporting (EDFacts)** — show a **SCHEMA** badge on their node and an **Explore Schema** button in their detail panel. Double-clicking the node also drills in.

Inside the schema view you can:
- **Browse the entity-relationship diagram** of the system's tables — each card lists its columns with PK/FK markers and data types. Drag table headers to rearrange (positions are saved), scroll to zoom, and drag the background to pan. Foreign-key relationships are drawn as connectors that follow the cards.
- **Click a table or a field** to open its governance panel.
- **Govern at the table and field level.** Every table and column inherits its governance (classification, PII/FERPA flags, owner, steward, retention, access restrictions, legal authority) from the parent system by default. Each value is tagged to show where it comes from — **Inherited**, **From table**, or **Override**.
- **Override only what differs.** Edit a table or field and set just the values that differ from the parent; leave the rest blank to keep inheriting. A small dot on a column marks fields that carry an override. Clearing a value reverts it to inheritance.

A breadcrumb at the top returns you to the high-level lineage diagram at any time.

> **Coming next (Phase 3):** true upstream field-level lineage — tracing where each column originates, the transformations applied along the way, and the responsible owners/stewards at each hop. The field panel already includes a placeholder section for this, pending column-level source mappings.

### Add New Records
- Use the **+ Add System** and **+ Add Flow** buttons in the toolbar
- New systems appear on the diagram immediately

## Project Structure

```
data-lineage-tracker/
  app.py              Python backend (Tornado + PostgreSQL)
  db.py               Database layer: connection pool + sqlite-style query shim
  auth.py             Password hashing, password policy, sessions, lockout
  permissions.py      Role hierarchy and permission checks
  audit.py            Append-only audit trail (field-level change tracking)
  requirements.txt    Python dependencies
  Dockerfile          Container image (portable to Render, AWS, or Azure)
  docker-compose.yml  Local stack: app + PostgreSQL
  .env.example        Environment variables reference
  alembic.ini         Migration configuration
  alembic/
    env.py            Reads DATABASE_URL
    versions/         Versioned schema migrations
  neo_schema.json          NEO database schema seed (tables, columns, foreign keys)
  fedreporting_schema.json Federal Reporting (EDFacts) database schema seed
  migrate_sqlite_to_postgres.py  One-time import from the old lineage.db
  selftest.py         End-to-end verification script
  lineage.db          Legacy SQLite database (migration source only; no longer used at runtime)
  static/
    index.html        Frontend (React, runs in the browser)
  README.md           This file
```

## Architecture Notes

**PostgreSQL, not SQLite.** All durable state lives in the database, which keeps
the app stateless and portable — the same container runs on Render today and on
AWS or Azure later by changing `DATABASE_URL`.

**Schema changes go through Alembic.** Add a migration rather than editing
tables by hand:
```bash
docker compose exec app alembic revision -m "describe the change"
# edit the generated file in alembic/versions/, then:
docker compose restart app        # migrations run automatically on startup
```

**Boolean-ish columns are stored as `INTEGER` (0/1)**, not `BOOLEAN`. The JSON
API and frontend both expect 0/1, so this preserves the API contract exactly.

**`db.py` translates `?` placeholders to `%s`** and returns rows that support
both `row['column']` and `row[0]`, so application queries keep the sqlite3-style
form they were written in.

## Data Model

The app stores these entity types:

| Entity | What It Represents |
|--------|-------------------|
| **System** | Any software, database, ETL process, or manual step in the ecosystem |
| **Data Flow** | A movement of data from one system to another, with all the details |
| **Data Element** | A specific data field (e.g., Student ID) with its definition and classification |
| **Stakeholder** | A person who owns or manages systems and flows |
| **Agreement** | A data sharing agreement governing interagency data exchanges |
| **Schema Table** | A physical database table belonging to a System, with governance overrides |
| **Schema Column** | A field within a schema table, with its own governance overrides (inheriting from the table, then the system) |
| **Contributor** | A person holding a governance role (data owner, steward). Formerly "Stakeholder". May optionally be linked to a user account. |
| **User** | A login account with a role. Users and contributors are separate: a contributor needs no login, and a user need not hold a governance role. |

See the **Data_Lineage_Tracker_Data_Model.docx** for the full schema reference.

## Seed Data

The app comes pre-loaded with the SIF/CONNECT data flow based on the state's SIF 3.x profile:

- **8 systems**: District SIS, CONNECT, NEO (ODS), LDS, EDFacts, Funding Calculations, Generate, District HR Systems
- **7 data flows**: across collection, reporting, funding, and longitudinal storage
- **33 data elements**: From the SIF profile (Student ID, enrollment, demographics, special ed, discipline, etc.)
- **NEO database schema** (from `neo_schema.json`): 56 tables and 649 columns across the `Ceds` and `core` schemas, with foreign-key relationships
- **Federal Reporting (EDFacts) schema** (from `fedreporting_schema.json`): 62 tables and ~2,650 columns across the `Assessment`, `NSC`, and `Reports` schemas

Database schemas are seeded independently of the main seed data, one file per system (`*_schema.json`). Seeding is idempotent per system, so an existing deployment gains each schema on its next start without losing records or re-seeding ones already present. To map another system's schema, drop in a new `<name>_schema.json` declaring its `system_id`.

## Migrating from the Old SQLite Database

A one-time script copies everything out of `lineage.db` into PostgreSQL. It only
reads the SQLite file and never modifies it.

```bash
docker compose up -d                                              # start the stack
docker compose exec app python migrate_sqlite_to_postgres.py --dry-run   # preview
docker compose exec app python migrate_sqlite_to_postgres.py --replace   # copy
```

The script copies tables parent-first so foreign keys are satisfied, matches
columns by name, reports any column present in one database but not the other,
and verifies row counts at the end. Without `--replace` it refuses to run if the
target tables already contain data, so it can't silently duplicate records.

## Resetting the Database

To wipe everything and start from seed data:
```bash
docker compose down -v      # deletes the database volume
docker compose up           # recreates the schema and re-seeds
```

## Deploying to Render

1. Create a **PostgreSQL** instance on Render (a paid instance — free ones expire and have no backups).
2. Create a **Web Service** from this repository and choose the **Docker** runtime.
3. In the web service's environment, set `DATABASE_URL` to the database's **Internal Database URL**.
4. Deploy. Migrations and seeding run automatically on startup.

> **Do not deploy on Render's free tier.** Free services have an ephemeral
> filesystem and spin down after 15 minutes of inactivity; with the previous
> SQLite setup that silently discarded every change. Using managed PostgreSQL
> avoids this entirely, but the database instance must be a paid one to get
> backups and point-in-time recovery.

**Backups.** Paid Render Postgres provides point-in-time recovery over a rolling
window (3 days on Hobby, 7 on Pro) plus downloadable exports retained 7 days.
That is disaster recovery, not records retention — schedule a periodic `pg_dump`
to storage you control if the lineage documentation has a retention requirement.

## Future Enhancements

- **Two-factor authentication and self-service password reset** — both need email delivery (Resend); the preference toggles already exist, locked off
- **Single sign-on (OIDC)** — federate with a state identity provider such as Microsoft Entra ID, replacing local passwords entirely
- **Agreement management screen** — the table and API exist; no UI yet
- **Field-level lineage tracing (Phase 3)** — trace each schema column back to its source element, the transformations applied, and the owners/stewards at each hop
- Map database schemas for additional systems (NEO and Federal Reporting are done)
- Stakeholder and Agreement management screens
- Search and filter the diagram by type, owner, PII status
- Export diagram as image
