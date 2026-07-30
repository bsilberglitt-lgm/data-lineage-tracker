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

### Verify everything works
```bash
python3 selftest.py
```
Exercises every endpoint, the governance inheritance chain, and the read/write
paths, then reports `PASS`/`FAIL` per check. Uses only the Python standard
library, so it runs from your Mac with nothing to install.

### Configuration
All configuration is via environment variables (no code changes needed to move
hosts):

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (required) | set by Docker Compose |
| `PORT` | Port to listen on | `8765` |

See `.env.example`.

## What You Can Do

### Lineage Diagram
- **Drag nodes** to rearrange the layout (positions are saved)
- **Click a system node** to see its details in the side panel
- **Click a flow line** (the arrows between nodes) to see transformation steps, business rules, validations, data elements carried, and more
- **Scroll to zoom** in and out
- **Click and drag the background** to pan

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

- **Real authentication (next up)** — the current password is hardcoded in client-side JavaScript and the API has no auth at all, so it is not suitable for multi-user access. Building on OIDC/SAML would allow single sign-on against the state's identity provider.
- **Per-user audit trail** — record who changed what, which the governance framework this app models would itself expect
- **Field-level lineage tracing (Phase 3)** — trace each schema column back to its source element, the transformations applied, and the owners/stewards at each hop
- Map database schemas for additional systems (NEO and Federal Reporting are done)
- Stakeholder and Agreement management screens
- Search and filter the diagram by type, owner, PII status
- Export diagram as image
