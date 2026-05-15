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

### Add New Records
- Use the **+ Add System** and **+ Add Flow** buttons in the toolbar
- New systems appear on the diagram immediately

## Project Structure

```
data-lineage-tracker/
  app.py              Python backend (Tornado + SQLite)
  requirements.txt    Python dependencies
  lineage.db          SQLite database (auto-created on first run)
  static/
    index.html        Frontend (React, runs in the browser)
  README.md           This file
```

## Data Model

The app stores five entity types:

| Entity | What It Represents |
|--------|-------------------|
| **System** | Any software, database, ETL process, or manual step in the ecosystem |
| **Data Flow** | A movement of data from one system to another, with all the details |
| **Data Element** | A specific data field (e.g., Student ID) with its definition and classification |
| **Stakeholder** | A person who owns or manages systems and flows |
| **Agreement** | A data sharing agreement governing interagency data exchanges |

See the **Data_Lineage_Tracker_Data_Model.docx** for the full schema reference.

## Seed Data

The app comes pre-loaded with the SIF/CONNECT data flow based on the state's SIF 3.x profile:

- **6 systems**: District SIS, CONNECT, ODS, LDS, EDFacts, Funding Calculations
- **5 data flows**: District-to-CONNECT, CONNECT-to-ODS, ODS-to-LDS, ODS-to-EDFacts, ODS-to-Funding
- **33 data elements**: From the SIF profile (Student ID, enrollment, demographics, special ed, discipline, etc.)
- **66 flow-element links**: Mapping which elements travel through which flows

## Resetting the Database

To start fresh, delete the `lineage.db` file and restart the app:
```bash
rm lineage.db
python3 app.py
```

## Future Enhancements

- Edit existing records (currently supports add)
- Stakeholder and Agreement management screens
- Search and filter the diagram by type, owner, PII status
- Export diagram as image
- User authentication for team access
- Deploy to AWS
