# Data Lineage Tracker

An interactive web application for tracking data lineage across the Department of Education's ecosystem. Visualize how data flows between systems, drill into transformation details, and maintain a living catalog of systems, data flows, data elements, stakeholders, and agreements.

## Quick Start

### Prerequisites
- **Python 3.8+** (check with `python3 --version`)
- **Tornado** web framework (the only dependency)

### Setup (one time)
```bash
cd data-lineage-tracker
pip install -r requirements.txt
```

### Run
```bash
python3 app.py
```

Then open **http://localhost:8765** in your browser.

The app will:
1. Create a SQLite database (`lineage.db`) in the project folder
2. Seed it with the SIF/CONNECT example data flow (6 systems, 5 flows, 33 data elements)
3. Start a web server on port 8765

### Change the port
```bash
PORT=9000 python3 app.py
```

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
  app.py              Python backend (Tornado + SQLite)
  requirements.txt    Python dependencies
  neo_schema.json          NEO database schema seed (tables, columns, foreign keys)
  fedreporting_schema.json Federal Reporting (EDFacts) database schema seed
  lineage.db          SQLite database (auto-created on first run)
  static/
    index.html        Frontend (React, runs in the browser)
  README.md           This file
```

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

## Resetting the Database

To start fresh, delete the `lineage.db` file and restart the app:
```bash
rm lineage.db
python3 app.py
```

## Future Enhancements

- **Field-level lineage tracing (Phase 3)** — trace each schema column back to its source element, the transformations applied, and the owners/stewards at each hop
- Map database schemas for additional systems (NEO and Federal Reporting are done)
- Stakeholder and Agreement management screens
- Search and filter the diagram by type, owner, PII status
- Export diagram as image
- User authentication for team access
- Deploy to AWS
