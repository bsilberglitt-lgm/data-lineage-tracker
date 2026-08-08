#!/usr/bin/env python3
"""Data Lineage Tracker — lightweight web application.

Run locally with Docker Compose:  docker compose up
Then open http://localhost:8765 in your browser.

Uses Tornado + PostgreSQL. The schema is managed by Alembic migrations
(see alembic/versions/), and database access goes through db.py, which keeps
the sqlite3-style calling convention used throughout this file.
"""

import asyncio, csv, io, json, os, datetime, re, sys, time
from pathlib import Path

# ---------------------------------------------------------------------------
# Try Tornado first; fall back to stdlib http.server
# ---------------------------------------------------------------------------
try:
    import tornado.ioloop
    import tornado.web
    HAS_TORNADO = True
except ImportError:
    HAS_TORNADO = False

from db import get_db, close_pool  # noqa: E402  (PostgreSQL connection pool)
import auth  # noqa: E402
import permissions  # noqa: E402
import audit  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_DIR = Path(__file__).parent


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    return dt.isoformat()


def parse_iso(text):
    try:
        return datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def run_migrations():
    """Bring the database schema up to date (Alembic).

    Running this at startup keeps deployment simple: the app is the single
    place that owns schema state, so Render, Docker Compose, and a local run
    all converge on the same schema with no extra release step.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_DIR / "alembic"))
    command.upgrade(cfg, "head")

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Governance inheritance
# ---------------------------------------------------------------------------
# Each tuple is (governance field on schema_table/schema_column, the matching
# field on the parent System row used as the inherited fallback). A None system
# field means the value only exists at table/column level (no system fallback).
GOV_FIELDS = [
    ("data_classification", "data_classification"),
    ("contains_pii", "contains_pii"),
    ("ferpa_protected", "ferpa_protected"),
    ("data_owner_id", "owner_id"),
    ("data_steward_id", "data_steward_id"),
    ("retention_schedule", "retention_schedule"),
    ("access_restrictions", "data_sharing_restrictions"),
    ("legal_authority", None),
]

# Governance override fields a client may write at the table/column level.
GOV_WRITABLE = [f for f, _ in GOV_FIELDS] + ["business_meaning", "notes"]

def _norm(v):
    """Treat empty string as NULL so clearing a field reverts to inheritance."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

def resolve_governance(system_row, table_row, column_row=None):
    """Resolve effective governance with provenance.

    Walks column -> table -> system, returning for each field the effective
    value and which level it came from ('column' | 'table' | 'system' | 'none').
    NULL at a level means "inherit"; 0 is a valid (non-inherited) override.
    """
    levels = []
    if column_row is not None:
        levels.append(("column", column_row))
    if table_row is not None:
        levels.append(("table", table_row))
    result = {}
    for field, sys_field in GOV_FIELDS:
        val, source = None, "none"
        for src_name, row in levels:
            v = row.get(field)
            if v is not None:
                val, source = v, src_name
                break
        if val is None and sys_field and system_row is not None:
            sv = system_row.get(sys_field)
            if sv is not None:
                val, source = sv, "system"
        result[field] = {"value": val, "source": source}
    return result

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def seed_db():
    conn = get_db()
    # Only seed if no systems exist yet
    if conn.execute("SELECT COUNT(*) FROM system").fetchone()[0] > 0:
        conn.close()
        return

    now = datetime.datetime.now().isoformat()

    # Stakeholders
    conn.execute("""INSERT INTO contributor (contributor_id,name,title,organization_unit,email,phone,
        role_type,notes,governance_role,governance_responsibilities,training_status,last_training_date,
        created_date,updated_date) VALUES
        ('CON-001','Data Integration Manager','Manager, Data Integration','Data Management Division',
         NULL,NULL,'Data Owner','Primary contact for SIF/CONNECT data pipeline',
         'Data Owner','Oversees data collection policies, approves data sharing agreements, ensures compliance with FERPA and state regulations',
         'Current','2025-09-15',?,?)""", (now, now))
    conn.execute("""INSERT INTO contributor (contributor_id,name,title,organization_unit,email,phone,
        role_type,notes,governance_role,governance_responsibilities,training_status,last_training_date,
        created_date,updated_date) VALUES
        ('CON-002','ODS Administrator','Database Administrator','IT Infrastructure',
         NULL,NULL,'System Admin','Manages the Operational Data Store infrastructure',
         'Data Steward','Day-to-day maintenance of ODS and LDS, data quality monitoring, backup and recovery operations',
         'Current','2025-10-01',?,?)""", (now, now))

    # Systems (with new governance fields)
    systems = [
        ("SYS-001", "District Student Information Systems",
         "Student Information Systems operated by local school districts (e.g., PowerSchool, Infinite Campus, Tyler SIS). These are the primary source of student demographic, enrollment, and discipline data.",
         "Source", "Various (PowerSchool, Infinite Campus, Tyler)", "SIS Platforms", "Production",
         "Local School Districts", "CON-001", "School Districts", 1, 1, "Active",
         "Multiple vendors across districts; data quality varies by district", 100, 250,
         "Confidential", "Varies by district", "Varies by vendor", "HTTPS/TLS 1.2", None, 0,
         None, "Per district policy", "Per district policy", "Varies", "Varies",
         "SIF 3.x with state extensions", "District staff with SIS access", "FERPA restrictions apply"),
        ("SYS-002", "CONNECT Integration Platform",
         "SIF-based integration middleware that receives data from district SIS platforms and routes it to the state ODS. Performs initial validation and SIF XML processing.",
         "ETL Process", "SIF Association / State-developed", "SIF Agent / Middleware", "Production",
         "Data Management Division", "CON-001", "Department of Education", 1, 1, "Active",
         "Uses SIF 3.x data model with state-specific extensions", 400, 250,
         "Confidential", "Role-based access", "AES-256", "TLS 1.2", "2025-06-15", 1,
         "CON-001", "7 years per state policy", "Secure deletion", "Daily", "Documented",
         "SIF 3.x", "Data Management Division staff", "No external sharing without agreement"),
        ("SYS-003", "NEO (State Operational Data Store)",
         "Central repository that receives validated data from CONNECT. Stores current-year operational data for all students, schools, and districts.",
         "Data Store", "State-developed", "SQL Server", "Production",
         "IT Infrastructure", "CON-002", "Department of Education", 1, 1, "Active",
         "Primary source of truth for current-year student data", 700, 250,
         "Confidential", "Role-based access with AD integration", "TDE (Transparent Data Encryption)", "TLS 1.2", "2025-08-01", 1,
         "CON-002", "7 years per state policy", "Secure deletion with verification", "Daily incremental, weekly full", "Full DR plan with 4-hour RTO",
         "State data model (derived from SIF/CEDS)", "Authorized DOE staff only", "Restricted to authorized personnel per FERPA"),
        ("SYS-004", "Longitudinal Data System (LDS)",
         "Long-term storage of historical student records spanning multiple years. Fed from the ODS after annual processing and validation.",
         "Data Store", "State-developed", "SQL Server / Data Warehouse", "Production",
         "IT Infrastructure", "CON-002", "Department of Education", 1, 1, "Active",
         "Contains historical records back to initial implementation", 700, 450,
         "Confidential", "Role-based access with AD integration", "TDE (Transparent Data Encryption)", "TLS 1.2", "2025-08-01", 1,
         "CON-002", "Permanent retention", "N/A - permanent archive", "Daily incremental, weekly full", "Full DR plan with 4-hour RTO",
         "State longitudinal data model", "Authorized research and reporting staff", "Strict FERPA controls; data sharing requires agreement"),
        ("SYS-005", "Federal Reporting (EDFacts)",
         "Federal reporting system for required compliance submissions including enrollment counts, assessment results, and program participation.",
         "Destination", "US Department of Education", "EDFacts Submission System (ESS)", "Production",
         "Data Management Division", "CON-001", "Federal Government", 1, 1, "Active",
         "Annual submissions required under ESEA/IDEA", 700, 50,
         "Confidential", "Credential-based (ESS login)", "Federal standard", "HTTPS/TLS", None, 1,
         None, "Per federal retention schedule", "Per federal policy", "N/A - federal system", "Federal DR",
         "EDFacts file specifications", "Authorized submission staff", "Federal disclosure avoidance rules apply"),
        ("SYS-006", "State Funding Calculations",
         "System used to calculate state education funding allocations based on enrollment, demographics, and program participation data from the ODS.",
         "Destination", "State-developed", "Custom Application", "Production",
         "Finance Division", None, "Department of Education", 1, 1, "Active",
         "Driven by state funding formula; uses ADM and program counts", 400, 450,
         "Sensitive", "Role-based access", "AES-256", "TLS 1.2", "2025-05-01", 1,
         None, "7 years per state policy", "Secure deletion", "Weekly", "Documented",
         "State funding formula specifications", "Finance Division staff", "Internal use only; aggregate data may be published"),
        ("SYS-007", "Generate",
         "EdFi Alliance's Generate application used to produce and submit federal EDFacts reports. Receives data from the Federal Reporting pipeline.",
         "Destination", "CIID / Ed-Fi Alliance", "Generate (Open Source)", "Production",
         "Data Management Division", "CON-001", "Department of Education", 1, 1, "Active",
         "Open-source tool for EDFacts file generation and submission", 950, 50,
         "Confidential", "Role-based access", "AES-256", "HTTPS/TLS", None, 1,
         None, "Per federal retention schedule", "Secure deletion", "N/A", "Documented",
         "EDFacts file specifications, CEDS", "Authorized reporting staff", "Federal disclosure avoidance rules apply"),
        ("SYS-008", "District HR Systems",
         "Human Resources and staffing systems operated by local school districts (e.g., Munis, Tyler, Frontline). These are the primary source of staff demographic, certification, assignment, and employment data.",
         "Source", "Various (Munis, Tyler, Frontline)", "HR/ERP Platforms", "Production",
         "Local School Districts", None, "School Districts", 1, 1, "Active",
         "Multiple vendors across districts; data includes staff demographics, credentials, assignments, and FTE", 100, 450,
         "Confidential", "Varies by district", "Varies by vendor", "HTTPS/TLS", None, 0,
         None, "Per district policy", "Per district policy", "Varies", "Varies",
         "State staff data collection specifications", "District HR staff", "FERPA and personnel privacy restrictions apply"),
    ]
    for s in systems:
        conn.execute("""INSERT INTO system (system_id,name,description,system_type,vendor,technology,
            environment,managed_by,owner_id,organization,contains_pii,ferpa_protected,status,notes,
            x_pos,y_pos,
            data_classification,access_method,encryption_at_rest,encryption_in_transit,last_security_review,has_audit_trail,
            data_steward_id,retention_schedule,destruction_method,backup_frequency,disaster_recovery,data_standards,
            authorized_users,data_sharing_restrictions,
            created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*s, now, now))

    # Data Flows (with new governance fields)
    flows = [
        ("PL-001", "District SIS to CONNECT (SIF Profile)",
         "Primary data collection pipeline. Districts transmit student demographic, enrollment, and discipline data to the state via the SIF/CONNECT integration platform. Data conforms to the state SIF 3.x profile with state-specific extensions.",
         "SYS-001", "SYS-002", "Inbound", "Daily", "SIF Agent (XML over HTTPS)", "SIF 3.x XML",
         "1. District SIS extracts data per SIF profile\n2. SIF Agent packages as SIF XML messages\n3. CONNECT receives and validates against SIF schema\n4. Field-level validation (required fields, data types, code sets)\n5. Cross-reference validation (school IDs, district IDs)\n6. Accepted records queued for ODS loading",
         "State-assigned IDs required (StateProvinceId for students, schools, LEAs)\nSIF extended elements carry state-specific data (ML program dates, special ed fields, truancy tracking)\nOptional fields become conditionally required based on program participation",
         "SIF schema validation on all inbound messages\nRequired field checks per optionality column (M/O/C/MC)\nCharacter length enforcement per SIF profile\nCode set validation against state-defined value lists\nCross-object referential integrity (StudentPersonalRefId, SchoolInfoRefId)",
         "SIF-level errors return error XML response to district agent\nField validation failures logged and flagged for district correction\nRecords with critical errors rejected; non-critical errors accepted with warnings",
         1, 1, "FERPA, State data collection statute", None, "CON-001", "Active",
         "Data quality varies significantly by district and SIS vendor\nSome districts still on older SIF 2.x format requiring translation",
         # New governance fields
         "Confidential", "TLS 1.2 (HTTPS)", "N/A - individual-level student data",
         "SIF schema validation, required field checks, code set validation, cross-reference integrity checks",
         "SIF 3.x with state extensions", "Per ODS retention schedule (7 years)",
         "FERPA, State data collection statute"),
        ("PL-002", "CONNECT to State ODS",
         "Validated data from CONNECT loaded into the Operational Data Store. Includes transformation from SIF XML structure to relational database schema.",
         "SYS-002", "SYS-003", "Inbound", "Daily", "Database ETL (SSIS)", "Relational (SQL Server)",
         "1. Extract validated SIF XML from CONNECT queue\n2. Parse XML into staging tables\n3. Map SIF object hierarchy to flat relational structure\n4. Apply state business rules and derived fields\n5. Deduplication check against existing ODS records\n6. Merge/upsert into ODS production tables",
         "Student deduplication using StateProvinceId as primary match key\nDerived fields calculated from raw data (e.g., age from BirthDate, enrollment duration)\nCode translations from SIF code sets to state reporting codes\nFiscal responsibility determined from ResidencyStatus",
         "Referential integrity checks against ODS master tables\nDuplicate detection across districts\nBusiness rule validation (e.g., enrollment dates within school year)\nData completeness checks for required reporting fields",
         "Staging table holds records pending review\nAutomated retry for transient failures\nManual review queue for business rule violations",
         1, 1, "State data management policy", None, "CON-002", "Active",
         "Deduplication logic needs refinement for students who transfer between districts mid-year",
         "Confidential", "Internal network (encrypted)", "N/A - internal transfer",
         "Deduplication checks, referential integrity, business rule validation, completeness checks",
         "State data model (SIF-to-relational mapping)", "7 years per state policy",
         "State data management policy"),
        ("PL-003", "ODS to Longitudinal Data System",
         "Annual load of validated, complete data from the ODS into the long-term longitudinal store.",
         "SYS-005", "SYS-004", "Outbound", "Annually", "Database ETL (SSIS)", "Relational (SQL Server)",
         "1. End-of-year snapshot from ODS\n2. Final validation and completeness checks\n3. Historical record matching and linking\n4. Load into LDS with school year tagging",
         "Student records linked across years using StateProvinceId\nSchool year attribution for all records\nPoint-in-time snapshot preserves data as of year-end",
         "Completeness checks for all required federal and state reporting fields\nHistorical consistency validation\nYear-over-year enrollment continuity checks",
         "Incomplete records flagged for manual review before LDS load\nMissing data reported to districts for correction",
         1, 1, "State statute, Federal longitudinal data requirements", None, "CON-002", "Active", None,
         "Confidential", "Internal network (encrypted)", "N/A - internal archive transfer",
         "Completeness checks, historical consistency, year-over-year continuity",
         "State longitudinal data model", "Permanent retention",
         "State statute, Federal longitudinal data requirements"),
        ("PL-004", "ODS to Federal Reporting (EDFacts)",
         "Extract and transform ODS data into required federal reporting formats for EDFacts submission.",
         "SYS-003", "SYS-005", "Outbound", "Annually", "File Generation + ESS Upload", "Fixed-width / CSV per EDFacts specs",
         "1. Extract relevant data from ODS\n2. Apply federal reporting business rules\n3. Aggregate to required reporting levels\n4. Generate EDFacts file specifications\n5. Validate against ESS edit checks\n6. Submit to EDFacts",
         "Federal aggregation rules differ from state definitions in some cases\nSuppression rules for small cell sizes (FERPA)\nCross-file consistency required across EDFacts submissions",
         "EDFacts edit checks (pre-submission)\nCross-file validation\nYear-over-year comparison for anomaly detection",
         "Edit check failures require data correction and resubmission\nESS provides detailed error reports",
         1, 1, "ESEA, IDEA, FERPA", None, "CON-001", "Active", "Tight federal submission deadlines; requires close coordination",
         "Confidential", "HTTPS/TLS (ESS upload)", "Small cell suppression (n<10), complementary suppression",
         "EDFacts edit checks, cross-file validation, year-over-year anomaly detection",
         "EDFacts file specifications", "Per federal retention schedule",
         "ESEA, IDEA, FERPA"),
        ("PL-005", "ODS to State Funding Calculations",
         "Enrollment and program participation data extracted from ODS for use in state funding formula calculations.",
         "SYS-003", "SYS-006", "Outbound", "Annually", "Database view / ETL", "Relational",
         "1. Extract ADM counts from ODS enrollment records\n2. Calculate weighted pupil counts per funding formula\n3. Apply program participation multipliers\n4. Generate funding calculation inputs",
         "Average Daily Membership (ADM) calculation from enrollment records\nWeighted student counts based on program participation (Special Ed, EL, economically disadvantaged)\nFunding formula applies multipliers to base per-pupil amount",
         "ADM counts reconciled against enrollment records\nProgram participation counts validated against eligibility data",
         "Discrepancies flagged for district verification\nAppeals process for disputed counts",
         1, 1, "State funding formula statute", None, "CON-001", "Active", None,
         "Sensitive", "Internal network (encrypted)", "Aggregate counts only; no individual student data in output",
         "ADM reconciliation, program participation validation",
         "State funding formula specifications", "7 years per state policy",
         "State funding formula statute"),
        ("PL-006", "Federal Reporting to Generate",
         "EDFacts data and file specifications passed to the Generate application for final file production, validation, and submission to the federal ESS system.",
         "SYS-005", "SYS-007", "Outbound", "Annually", "File Transfer / Application Integration", "EDFacts file formats (fixed-width / CSV)",
         "1. EDFacts reporting data prepared in federal reporting system\n2. Data transferred to Generate for file production\n3. Generate applies EDFacts file specifications\n4. Files validated against ESS edit checks\n5. Approved files submitted to federal ESS",
         "EDFacts file specifications define required formats and aggregation levels\nGenerate applies CEDS-aligned business rules\nCross-file consistency enforced across submissions",
         "ESS edit check validation prior to submission\nCross-file consistency checks\nYear-over-year trend analysis for anomaly detection",
         "Validation failures returned for correction\nGenerate provides detailed error reports and resubmission workflow",
         1, 1, "ESEA, IDEA, FERPA", None, "CON-001", "Active",
         "Tight federal submission deadlines; Generate version updates require testing",
         "Confidential", "HTTPS/TLS", "Small cell suppression per federal rules",
         "ESS edit checks, cross-file validation, year-over-year anomaly detection",
         "EDFacts file specifications, CEDS", "Per federal retention schedule",
         "ESEA, IDEA, FERPA"),
        ("PL-007", "District HR to NEO (Staff Data)",
         "Staff demographic, certification, assignment, and employment data collected from district HR systems and loaded directly into the NEO Operational Data Store.",
         "SYS-008", "SYS-003", "Inbound", "Daily", "Secure File Transfer (SFTP) / API", "CSV / Fixed-width",
         "1. District HR system extracts staff data per state collection specs\n2. Files transmitted via SFTP or API to state infrastructure\n3. File format and schema validation\n4. Staff ID matching and deduplication\n5. Credential and certification cross-referencing\n6. Load into NEO staff tables",
         "State-assigned Educator ID used as primary match key\nCertification records cross-referenced with state certification database\nFTE calculations based on assignment records\nMultiple assignments per staff member allowed",
         "Schema validation on inbound files\nRequired field checks (name, DOB, Educator ID, assignment)\nCertification validation against state records\nFTE totals validated (should not exceed 1.0 without explanation)\nDuplicate detection across districts",
         "Validation failures reported back to districts for correction\nRecords with critical errors rejected; warnings issued for non-critical issues\nDistricts given correction window before reporting deadlines",
         1, 1, "State staff data collection statute, FERPA", None, "CON-001", "Active",
         "Data quality varies by district; some districts submit manually via spreadsheet upload",
         "Confidential", "SFTP / HTTPS", "N/A - individual staff records",
         "Schema validation, required field checks, certification cross-reference, FTE validation, deduplication",
         "State staff data collection specifications", "7 years per state policy",
         "State staff data collection statute, FERPA"),
    ]
    for f in flows:
        conn.execute("""INSERT INTO data_flow (flow_id,name,description,source_system_id,destination_system_id,
            direction,frequency,transfer_method,format,transformation_steps,business_rules,validations,
            error_handling,contains_pii,ferpa_protected,regulatory_driver,agreement_id,owner_id,status,
            known_issues,
            data_classification,encryption_method,disclosure_avoidance,
            data_quality_measures,data_standard,retention_at_destination,
            legal_authority,
            created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*f, now, now))

    # Data Elements (seeded from SIF Profile)
    elements = [
        ("DE-001", "State Student ID (StateProvinceId)", "SASID, SSID, State ID", "Unique student identifier assigned by the state student information system. 9 or 10 characters.", "varchar(10)", "9-10 digit numeric", 1, 1, "SYS-001", "Primary key for student matching across systems; appears in StudentPersonal object",
         "Confidential", "SIF 3.x / CEDS", "The primary identifier used to uniquely track a student across all state education systems, districts, and reporting periods", "CON-001", "CON-002"),
        ("DE-002", "Local Student ID (LocalId)", "School Student ID, District ID", "School or district-level student identification number. Up to 25 characters.", "varchar(25)", "Alphanumeric, 0-25 chars", 1, 0, "SYS-001", "District-assigned; may not be unique across districts",
         "Sensitive", "SIF 3.x", "A district-assigned identifier for internal tracking; not guaranteed unique across districts", None, None),
        ("DE-003", "Student Name (First/Middle/Last)", "FirstName, MiddleName, LastName", "Legal name components of the student including first, middle, last, preferred name, and suffix.", "varchar(50) each", "Alpha, 2-50 chars for first/last", 1, 1, "SYS-001", "No aliases or suffixes in first/last name fields",
         "Confidential", "SIF 3.x", "The legal name of the student as recorded in district systems", None, None),
        ("DE-004", "Date of Birth (BirthDate)", "DOB, Birthday", "Student date of birth in ISO format.", "date", "YYYY-MM-DD format", 1, 1, "SYS-001", "Mandatory in StudentPersonal Demographics",
         "Confidential", "SIF 3.x", "Student date of birth; used for age calculations and grade placement verification", None, None),
        ("DE-005", "Gender", None, "Student gender as reported in demographics.", "char(1)", "State-defined code set", 1, 1, "SYS-001", "Mandatory in StudentPersonal Demographics",
         "Confidential", "SIF 3.x", "Demographic gender classification for reporting purposes", None, None),
        ("DE-006", "Race/Ethnicity", "RaceList, HispanicLatino", "Student race and Hispanic/Latino indicator. Multiple races may be reported.", "varchar", "Federal race/ethnicity categories", 1, 1, "SYS-001", "Two fields: Race code(s) and Hispanic/Latino indicator; both mandatory",
         "Confidential", "SIF 3.x / Federal categories", "Federal race and ethnicity categories used for civil rights and equity reporting", None, None),
        ("DE-007", "Physical Address", "Street, City, State, Zip", "Student physical (residential) address. Address type 0765.", "composite", "Standard US address format", 1, 1, "SYS-001", "PO Box not accepted for physical address; zip code numbers only, no dashes",
         "Confidential", "SIF 3.x", "Student residential address used for district assignment and geographic reporting", None, None),
        ("DE-008", "Mailing Address", "Mail Address", "Student mailing address if different from physical. Address type 0123.", "composite", "Standard US address format", 1, 0, "SYS-001", "Optional; defaults to physical if not provided",
         "Confidential", "SIF 3.x", "Optional mailing address when different from physical address", None, None),
        ("DE-009", "LEA StateProvinceId", "SAU ID, District ID, LEA ID", "NEO Organization ID for the attending School Administrative Unit. 1-4 characters.", "varchar(4)", "1-4 digit numeric", 0, 0, "SYS-001", "Links student to their LEA/SAU; from LEAInfo object",
         "Internal", "SIF 3.x", "State-assigned district identifier linking students to their school administrative unit", None, None),
        ("DE-010", "School StateProvinceId", "School ID, School Code", "State-assigned school identifier. Used in SchoolInfo object.", "varchar(4)", "1-4 digit numeric", 0, 0, "SYS-001", "Links enrollment to specific school",
         "Internal", "SIF 3.x", "State-assigned school code linking enrollment records to specific schools", None, None),
        ("DE-011", "School Name", None, "Official name of the school.", "varchar", "Free text", 0, 0, "SYS-001", "From SchoolInfo object",
         "Public", "SIF 3.x", "Official school name as registered with the state", None, None),
        ("DE-012", "Enrollment Entry Date", "EntryDate, Start Date", "Date student enrolled at the school.", "date", "YYYY-MM-DD", 1, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment",
         "Confidential", "SIF 3.x", "The date a student begins enrollment at a school; critical for ADM calculations", None, None),
        ("DE-013", "Enrollment Entry Type", "EntryType, Entry Code", "Code indicating the type of enrollment entry. Includes state-specific OtherCodeList.", "varchar(5)", "State-defined 5-digit code set", 0, 1, "SYS-001", "Uses OtherCodeList with StateProvince codeset",
         "Sensitive", "SIF 3.x / State codes", "Classification of how the student entered the school (transfer, new enrollment, etc.)", None, None),
        ("DE-014", "Grade Level", "GradeLevel, Grade", "Student grade level at the enrolled school.", "varchar(2)", "State-defined grade codes", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment",
         "Sensitive", "SIF 3.x", "Student grade placement; used for cohort tracking and reporting", None, None),
        ("DE-015", "Enrollment Exit Date", "ExitDate, End Date, Withdrawal Date", "Date student exited/withdrew from the school.", "date", "YYYY-MM-DD or empty", 1, 1, "SYS-001", "Conditional: required when student exits",
         "Confidential", "SIF 3.x", "Date a student leaves a school; triggers exit-related reporting", None, None),
        ("DE-016", "Enrollment Exit Type", "ExitType, Exit Code, Withdrawal Code", "Code indicating the reason for enrollment exit.", "varchar(5)", "State-defined 5-digit code set", 0, 1, "SYS-001", "Uses OtherCodeList with StateProvince codeset",
         "Sensitive", "SIF 3.x / State codes", "Reason for student departure (graduated, transferred, dropped out, etc.)", None, None),
        ("DE-017", "Full-Time/Part-Time Status (FTPTStatus)", "FTE Status", "Indicates whether the student is enrolled full-time or part-time.", "char(1)", "0 or 1", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment; legacy SIF2 users can use extended element",
         "Sensitive", "SIF 3.x", "Full-time equivalency status affecting funding calculations", None, None),
        ("DE-018", "Residency/Fiscal Responsibility", "ResidencyStatus", "Indicates the student fiscal responsibility status.", "char(1)", "R and other state codes", 0, 1, "SYS-001", "Uses OtherCodeList; determines funding responsibility",
         "Sensitive", "SIF 3.x / State codes", "Determines which district is fiscally responsible for the student", None, None),
        ("DE-019", "Special Education Status", "SPED, IEP, SpecialEd fields", "Collection of special education fields: start date, exit date, exit reason, setting, disability code, and alternate assessment flag.", "composite", "Various per field", 1, 1, "SYS-001", "Six related SIF_ExtendedElements; conditional on student having IEP",
         "Confidential", "SIF 3.x / IDEA", "Special education program participation data required for IDEA compliance reporting", "CON-001", None),
        ("DE-020", "Economic Disadvantage Status", "EconomicStatus, FRL, Free/Reduced Lunch", "Student economic disadvantage eligibility status including type, start date, and end date.", "composite", "State-defined eligibility codes", 1, 1, "SYS-001", "Three related SIF_ExtendedElements; conditional on eligibility",
         "Confidential", "SIF 3.x / State codes", "Economic disadvantage indicators used for Title I eligibility and funding calculations", None, None),
        ("DE-021", "English Learner / ML Program", "EL, ML, ELL, Bilingual, ESL", "Multilingual Learner program participation fields including ML start date, first US school entry date, native language(s), interpreter needs, and specific program types.", "composite", "Various per field", 1, 1, "SYS-001", "Multiple SIF_ExtendedElements covering 5 language slots; program types include Transitional Bilingual, Dual Language, ESL/ELD, Content Classes, Newcomer",
         "Confidential", "SIF 3.x / State codes", "Multilingual learner program participation data for Title III compliance and services tracking", "CON-001", None),
        ("DE-022", "Homeless Status", "McKinney-Vento, Homeless", "Homeless indicators including nighttime residence code and unaccompanied youth status.", "composite", "State-defined homeless codes", 1, 1, "SYS-001", "Two SIF_ExtendedElements; conditional on student being identified as homeless",
         "Confidential", "SIF 3.x / McKinney-Vento", "McKinney-Vento homeless status indicators for federal compliance reporting", None, None),
        ("DE-023", "Title I-A Eligibility", "Title1A, Title I", "Title I-A eligibility flags for Math and ELA.", "char(1) each", "NULL/N/Y", 1, 1, "SYS-001", "Two separate flags: Title1AFlagMath and Title1AFlagELA",
         "Confidential", "SIF 3.x / ESEA", "Title I-A program eligibility for federal funding and reporting", None, None),
        ("DE-024", "Military Connected Student", "MilitaryConnected", "Indicates if student has parent(s) in uniformed services.", "varchar", "ActiveDuty, NationalGuard, etc.", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment; based on annual parent survey",
         "Sensitive", "SIF 3.x / Federal", "Military family connection status for federal impact aid reporting", None, None),
        ("DE-025", "Discipline Incident", "Incident, Behavior", "Discipline incident record including incident number, date, category, weapon type, offender info, actions taken, duration, and description.", "composite", "Various per field", 1, 1, "SYS-001", "From DisciplineIncident SIF object; links to students via OffenderList",
         "Confidential", "SIF 3.x / CRDC", "Discipline event data required for Civil Rights Data Collection and school safety reporting", "CON-001", None),
        ("DE-026", "Truancy Data", "Truancy, Attendance", "Truancy tracking fields including type, superintendent notification date, intervention plan date, parent notification, board notification, parent meeting, law enforcement referral, and additional interventions.", "composite", "Various per field", 1, 1, "SYS-001", "Eight SIF_ExtendedElements on StudentSchoolEnrollment; conditional on incident category 1240",
         "Confidential", "SIF 3.x / State law", "State-mandated truancy tracking for compliance with attendance laws", None, None),
        ("DE-027", "Section 504 Status", "504, Chapter504", "Indicates if student participates in a Chapter 504 program.", "char(1)", "NULL/N/Y", 1, 1, "SYS-001", "Conditional SIF_ExtendedElement",
         "Confidential", "SIF 3.x / Section 504", "Section 504 accommodation plan status for civil rights compliance", None, None),
        ("DE-028", "Gifted and Talented", "GT, Gifted", "Indicates student participation in Gifted and Talented programs, both Academic and Artistic.", "char(1) each", "NULL/N/Y", 0, 1, "SYS-001", "Two separate flags: GiftedTalentedAcademic and GiftedTalentedArtistic",
         "Sensitive", "SIF 3.x / State", "Gifted and talented program participation for state reporting", None, None),
        ("DE-029", "Resident Town (GeoCode)", "ResidentTown, TownCode", "ID of the town the student resides in.", "varchar(5)", "5-digit town code", 0, 0, "SYS-001", "Mandatory in StudentSchoolEnrollment; uses state geo code lookup",
         "Sensitive", "SIF 3.x / State codes", "Geographic code for student residence; used for district assignment and geographic analysis", None, None),
        ("DE-030", "Parent/Guardian Names", "Parent, Guardian", "First and last names of up to two parents or guardians.", "varchar(50) each", "Alpha, up to 50 chars", 1, 1, "SYS-001", "Four SIF_ExtendedElements; ampersand not accepted; first/last are paired (one requires the other)",
         "Confidential", "SIF 3.x", "Parent/guardian contact information for family engagement and emergency contact", None, None),
        ("DE-031", "Immigrant Status", "Immigrant", "Indicates whether the student is an immigrant.", "char(1)", "NULL/N/Y", 1, 1, "SYS-001", "Optional field on StudentPersonal",
         "Confidential", "SIF 3.x / Federal", "Immigration status indicator for Title III immigrant student reporting", None, None),
        ("DE-032", "Home Instruction Flag", "HomeInstruction, Home School", "Indicates students who are home instructed and attend local school part time or receive only Special Education services.", "char(1)", "NULL/N/Y", 0, 1, "SYS-001", "If yes, FTE value may be required",
         "Sensitive", "SIF 3.x / State", "Home instruction status affecting enrollment counting and FTE calculations", None, None),
        ("DE-033", "Membership Type", "MembershipType, Primary/Secondary", "Indicates whether the enrollment is the student primary or secondary enrollment.", "char(1)", "P or S", 0, 1, "SYS-001", "Attribute on StudentSchoolEnrollment object",
         "Sensitive", "SIF 3.x", "Primary vs secondary enrollment designation affecting ADM calculations and reporting", None, None),
    ]
    for e in elements:
        conn.execute("""INSERT INTO data_element (element_id,name,aliases,definition,data_type,
            valid_values,is_pii,ferpa_protected,source_of_truth,notes,
            data_classification,data_standard,business_meaning,data_owner_id,data_steward_id,
            created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*e, now, now))

    # Flow-Element junction (PL-001 carries all elements into CONNECT)
    for i in range(1, 34):
        eid = f"DE-{i:03d}"
        transformed = 1 if i in (19, 20, 21, 25, 26) else 0
        note = None
        if i == 1:
            note = "Used as primary matching key"
        elif i == 19:
            note = "Six sub-fields combined into composite special education record"
        elif i == 21:
            note = "Multiple language/interpreter fields consolidated"
        elif i == 25:
            note = "Hierarchical incident structure flattened"
        conn.execute("""INSERT INTO flow_element (flow_id, element_id, is_transformed, transformation_note)
            VALUES (?, ?, ?, ?)""", ("PL-001", eid, transformed, note))

    # PL-002 carries most elements too (CONNECT to ODS), with more transformations
    for i in range(1, 34):
        eid = f"DE-{i:03d}"
        conn.execute("""INSERT INTO flow_element (flow_id, element_id, is_transformed, transformation_note)
            VALUES (?, ?, ?, ?)""", ("PL-002", eid, 1, "SIF XML hierarchy mapped to relational database columns"))

    conn.commit()
    conn.close()
    print("Database seeded with baseline systems, flows, and data elements")

# ---------------------------------------------------------------------------
# Schema drill-down seeding (loads neo_schema.json into schema_table/column)
# ---------------------------------------------------------------------------

def seed_schema():
    """Populate schema_table / schema_column from any *_schema.json seed files.

    Each seed file declares the system_id it belongs to (e.g. neo_schema.json ->
    SYS-003, fedreporting_schema.json -> SYS-005). Runs independently of
    seed_db() so an already-seeded deployed database still gains schemas.

    Idempotency is per-system: a system is only seeded if it has no schema_table
    rows yet, so adding a new schema file seeds just that system and leaves
    existing ones untouched.
    """
    seed_files = sorted(Path(__file__).parent.glob("*_schema.json"))
    if not seed_files:
        return
    conn = get_db()
    now = datetime.datetime.now().isoformat()

    for path in seed_files:
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            print(f"  Skipping schema seed {path.name}: {e}")
            continue

        system_id = data.get("system_id")
        if not system_id:
            continue
        # Parent system must exist in this database
        if not conn.execute("SELECT 1 FROM system WHERE system_id=?", (system_id,)).fetchone():
            continue
        # Per-system idempotency: skip if this system already has a schema
        if conn.execute("SELECT COUNT(*) FROM schema_table WHERE system_id=?",
                        (system_id,)).fetchone()[0] > 0:
            continue

        tables = data.get("tables", [])
        cols = data.get("columns", [])
        for t in tables:
            conn.execute(
                """INSERT INTO schema_table
                   (table_id, system_id, schema_name, table_name, primary_key,
                    x_pos, y_pos, created_date, updated_date)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (t["table_id"], system_id, t.get("schema_name"), t.get("table_name"),
                 t.get("primary_key"), t.get("x_pos", 0), t.get("y_pos", 0), now, now))
        for c in cols:
            conn.execute(
                """INSERT INTO schema_column
                   (column_id, table_id, column_name, ordinal, data_type, is_nullable,
                    is_identity, is_pk, is_fk, default_value, fk_target_schema,
                    fk_target_table, fk_target_column, fk_target_table_id, fk_constraint,
                    created_date, updated_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (c["column_id"], c["table_id"], c.get("column_name"), c.get("ordinal"),
                 c.get("data_type"), c.get("is_nullable", 1), c.get("is_identity", 0),
                 c.get("is_pk", 0), c.get("is_fk", 0), c.get("default_value"),
                 c.get("fk_target_schema"), c.get("fk_target_table"),
                 c.get("fk_target_column"), c.get("fk_target_table_id"),
                 c.get("fk_constraint"), now, now))
        conn.commit()
        print(f"Schema seeded: {len(tables)} tables, {len(cols)} columns for {system_id} ({path.name})")

    conn.close()

# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
# Global settings a SuperAdmin can change without a code change. Each state
# deploys its own instance, so these are per-deployment rather than per-tenant.

DEFAULT_PREFERENCES = [
    # key, value, type, label, description, category, editable, order
    ("app_title", "DATA LINEAGE TRACKER", "string", "Application title",
     "Shown in the sidebar header.", "General", 1, 10),
    ("app_subtitle", "Dept. of Education", "string", "Application subtitle",
     "Shown under the title in the sidebar.", "General", 1, 20),

    ("allowed_email_domains", "", "string", "Allowed email domains",
     "Comma-separated list of domains permitted for new accounts "
     "(e.g. maine.gov, k12.me.us). Leave blank to allow any domain.",
     "Accounts", 1, 30),
    ("default_role", permissions.VIEWER, "role", "Default role for new users",
     "Pre-selected when an administrator creates an account.", "Accounts", 1, 40),

    ("idle_timeout_hours", "8", "number", "Idle timeout (hours)",
     "How long a session can sit idle before the user must sign in again.",
     "Security", 1, 50),

    ("password_min_length", "12", "number", "Minimum password length",
     "Minimum number of characters required.", "Password policy", 1, 60),
    ("password_require_uppercase", "true", "boolean", "Require an uppercase letter",
     "", "Password policy", 1, 70),
    ("password_require_lowercase", "true", "boolean", "Require a lowercase letter",
     "", "Password policy", 1, 80),
    ("password_require_number", "true", "boolean", "Require a number",
     "", "Password policy", 1, 90),
    ("password_require_symbol", "true", "boolean", "Require a symbol",
     "", "Password policy", 1, 100),

    # Phase 2 — the plumbing is in place; the flows are not built yet, so these
    # are shown but locked off.
    ("require_2fa", "false", "boolean", "Require two-factor authentication",
     "Not yet available. Requires email delivery to be configured.",
     "Coming soon", 0, 110),
    ("enable_self_service_reset", "false", "boolean", "Allow self-service password reset",
     "Not yet available. Requires email delivery to be configured.",
     "Coming soon", 0, 120),
]

_PREF_CACHE = {"values": None, "loaded_at": 0}
PREF_CACHE_SECONDS = 10


def ensure_preferences(conn=None):
    """Insert any preference keys that don't exist yet (safe to run repeatedly)."""
    own = conn is None
    conn = conn or get_db()
    now = iso(utcnow())
    existing = {r["key"] for r in conn.execute("SELECT key FROM preference").fetchall()}
    added = 0
    for key, value, vtype, label, desc, cat, editable, order in DEFAULT_PREFERENCES:
        if key in existing:
            # Keep the descriptive metadata current without touching the value.
            conn.execute(
                """UPDATE preference SET label=?, description=?, category=?,
                       value_type=?, editable=?, sort_order=? WHERE key=?""",
                (label, desc, cat, vtype, editable, order, key))
            continue
        conn.execute(
            """INSERT INTO preference
                 (key, value, value_type, label, description, category,
                  editable, sort_order, updated_date)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (key, value, vtype, label, desc, cat, editable, order, now))
        added += 1
    conn.commit()
    if own:
        conn.close()
    _PREF_CACHE["values"] = None
    if added:
        print(f"Preferences: added {added} new setting(s)")


def coerce_pref(value, value_type):
    if value_type == "number":
        try:
            return float(value) if "." in str(value) else int(value)
        except (TypeError, ValueError):
            return 0
    if value_type == "boolean":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    return value


def get_preferences(conn=None, refresh=False):
    """Return {key: typed value}, cached briefly to avoid a query per request."""
    now = time.time()
    if (not refresh and _PREF_CACHE["values"] is not None
            and now - _PREF_CACHE["loaded_at"] < PREF_CACHE_SECONDS):
        return _PREF_CACHE["values"]
    own = conn is None
    conn = conn or get_db()
    rows = conn.execute("SELECT key, value, value_type FROM preference").fetchall()
    if own:
        conn.close()
    values = {r["key"]: coerce_pref(r["value"], r["value_type"]) for r in rows}
    _PREF_CACHE["values"] = values
    _PREF_CACHE["loaded_at"] = now
    return values


def invalidate_preferences():
    _PREF_CACHE["values"] = None


# ---------------------------------------------------------------------------
# First-run bootstrap: create the initial SuperAdmin and backfill authorship
# ---------------------------------------------------------------------------

BACKFILL_TABLES = [
    ("contributor", "contributor_id"),
    ("system", "system_id"),
    ("agreement", "agreement_id"),
    ("data_flow", "flow_id"),
    ("data_element", "element_id"),
    ("schema_table", "table_id"),
    ("schema_column", "column_id"),
]


def reset_superadmin_if_requested(conn):
    """Emergency password reset for a SuperAdmin, driven by environment variables.

    Until self-service password reset exists (Phase 2), an administrator who
    loses access has no way back in. Setting BOOTSTRAP_SUPERADMIN_RESET=true
    together with BOOTSTRAP_SUPERADMIN_EMAIL and BOOTSTRAP_SUPERADMIN_PASSWORD
    resets that account's password on the next start and forces a change at the
    next sign-in.

    Only someone who can already change the deployment's environment variables
    can trigger this, and every use is written to the audit log. Remove the
    variable afterwards so a later restart doesn't silently reset the password
    again.
    """
    if str(os.environ.get("BOOTSTRAP_SUPERADMIN_RESET", "")).strip().lower() \
            not in ("1", "true", "yes", "on"):
        return

    email = auth.normalize_email(os.environ.get("BOOTSTRAP_SUPERADMIN_EMAIL", ""))
    password = os.environ.get("BOOTSTRAP_SUPERADMIN_PASSWORD", "").strip()
    if not email or not password:
        print("\n  BOOTSTRAP_SUPERADMIN_RESET is set, but BOOTSTRAP_SUPERADMIN_EMAIL "
              "and BOOTSTRAP_SUPERADMIN_PASSWORD are both required. Skipping.\n",
              flush=True)
        return

    row = conn.execute("SELECT * FROM app_user WHERE email=?", (email,)).fetchone()
    now = iso(utcnow())
    if row:
        conn.execute(
            """UPDATE app_user
                  SET password_hash=?, must_change_password=1, is_active=1,
                      role=?, updated_date=?
                WHERE user_id=?""",
            (auth.hash_password(password), permissions.SUPERADMIN, now, row["user_id"]))
        conn.execute("UPDATE user_session SET revoked_at=? WHERE user_id=? "
                     "AND revoked_at IS NULL", (now, row["user_id"]))
        user_id = row["user_id"]
        what = "reset"
    else:
        created = conn.execute(
            """INSERT INTO app_user
                 (email, first_name, last_name, password_hash, role, is_active,
                  must_change_password, created_date, updated_date)
               VALUES (?,?,?,?,?,1,1,?,?)
               RETURNING user_id""",
            (email,
             os.environ.get("BOOTSTRAP_SUPERADMIN_FIRST_NAME", "System"),
             os.environ.get("BOOTSTRAP_SUPERADMIN_LAST_NAME", "Administrator"),
             auth.hash_password(password), permissions.SUPERADMIN, now, now)).fetchone()
        user_id = created["user_id"]
        what = "created"

    audit.record_event(conn, {"user_id": user_id, "email": email}, audit.PASSWORD_CHANGED,
                       "user", user_id,
                       summary="SuperAdmin password reset via BOOTSTRAP_SUPERADMIN_RESET")
    conn.commit()

    print("\n" + "=" * 68, flush=True)
    print(f"  SuperAdmin account {what}: {email}", flush=True)
    print("  Password set from BOOTSTRAP_SUPERADMIN_PASSWORD.", flush=True)
    print("  You must change it at next sign-in.", flush=True)
    print("  >> Now REMOVE BOOTSTRAP_SUPERADMIN_RESET from the environment. <<", flush=True)
    print("=" * 68 + "\n", flush=True)


def bootstrap_superadmin():
    """Create the first SuperAdmin from environment variables, once.

    Set BOOTSTRAP_SUPERADMIN_EMAIL (and optionally _PASSWORD, _FIRST_NAME,
    _LAST_NAME). If no password is given, a strong one is generated and printed
    to the logs a single time. The account must change its password at first
    login either way.
    """
    conn = get_db()
    try:
        if conn.execute("SELECT COUNT(*) FROM app_user").fetchone()[0] > 0:
            reset_superadmin_if_requested(conn)
            return
        email = auth.normalize_email(os.environ.get("BOOTSTRAP_SUPERADMIN_EMAIL", ""))
        if not email:
            print("\n  No users exist yet and BOOTSTRAP_SUPERADMIN_EMAIL is not set.")
            print("  Set it (and optionally BOOTSTRAP_SUPERADMIN_PASSWORD) and restart "
                  "to create the first SuperAdmin.\n")
            return

        import secrets as _secrets
        password = os.environ.get("BOOTSTRAP_SUPERADMIN_PASSWORD", "").strip()
        generated = False
        if not password:
            password = _secrets.token_urlsafe(12) + "aA1!"
            generated = True

        now = iso(utcnow())
        row = conn.execute(
            """INSERT INTO app_user
                 (email, first_name, last_name, password_hash, role, is_active,
                  must_change_password, created_date, updated_date)
               VALUES (?,?,?,?,?,1,1,?,?)
               RETURNING user_id""",
            (email,
             os.environ.get("BOOTSTRAP_SUPERADMIN_FIRST_NAME", "System"),
             os.environ.get("BOOTSTRAP_SUPERADMIN_LAST_NAME", "Administrator"),
             auth.hash_password(password), permissions.SUPERADMIN, now, now),
        ).fetchone()
        user_id = row["user_id"]
        conn.commit()

        actor = {"user_id": user_id, "email": email}
        audit.record_event(conn, actor, audit.CREATE, "user", user_id,
                           summary=f"Initial SuperAdmin account created for {email}")

        # Attribute pre-existing records to this account, as agreed: they were
        # created before auditing existed. One event per table keeps the log
        # readable instead of generating thousands of synthetic entries.
        for table, _pk in BACKFILL_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if not n:
                continue
            conn.execute(f"UPDATE {table} SET created_by=?, updated_by=? "
                         f"WHERE created_by IS NULL", (user_id, user_id))
            audit.record_event(
                conn, actor, audit.BACKFILL, table, None,
                summary=f"{n} pre-existing {table} record(s) attributed to "
                        f"{email}; created before audit logging began")
        conn.commit()

        print("\n" + "=" * 68, flush=True)
        print("  Initial SuperAdmin created", flush=True)
        print(f"    Email:    {email}", flush=True)
        if generated:
            print(f"    Password: {password}", flush=True)
            print("    (shown once — you must change it at first login)", flush=True)
        else:
            print("    Password: as supplied in BOOTSTRAP_SUPERADMIN_PASSWORD", flush=True)
            print("    (you must change it at first login)", flush=True)
        print("=" * 68 + "\n", flush=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tornado handlers
# ---------------------------------------------------------------------------

if HAS_TORNADO:

    class BaseAPIHandler(tornado.web.RequestHandler):
        """Base for every API endpoint: authentication, roles, CSRF, auditing.

        Permissions are declared per handler rather than repeated in each
        method. ``READ_ROLE`` guards GET/HEAD; ``WRITE_ROLE`` guards everything
        else. Subclasses override these to raise the bar (for example the user
        management endpoints require Administrator).

        These checks run server-side on every request. The frontend also hides
        controls a user cannot use, but that is only a convenience — this is the
        actual security boundary.
        """

        requires_auth = True
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.EDITOR
        # Endpoints usable while an account still has a forced password change
        # pending (otherwise the user could never clear the flag).
        allow_when_password_expired = False
        SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

        def initialize(self):
            self.current_session = None
            self.current_user = None

        def set_default_headers(self):
            self.set_header("Content-Type", "application/json")
            self.set_header("X-Content-Type-Options", "nosniff")
            self.set_header("X-Frame-Options", "DENY")
            self.set_header("Referrer-Policy", "same-origin")

        def write_json(self, data):
            self.write(json.dumps(data, default=str))

        def get_json_body(self):
            try:
                return json.loads(self.request.body)
            except Exception:
                return {}

        # -- request metadata ------------------------------------------------
        def client_ip(self):
            forwarded = self.request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
            return self.request.remote_ip

        def user_agent(self):
            return (self.request.headers.get("User-Agent") or "")[:400]

        def is_secure_request(self):
            proto = self.request.headers.get("X-Forwarded-Proto", self.request.protocol)
            return proto == "https"

        def fail(self, status, message, **extra):
            self.set_status(status)
            payload = {"error": message}
            payload.update(extra)
            self.write_json(payload)
            self.finish()

        # -- session ---------------------------------------------------------
        def load_session(self):
            """Validate the session cookie. Returns (user, session) or (None, None)."""
            raw = self.get_cookie(auth.SESSION_COOKIE)
            if not raw:
                return None, None
            token_hash = auth.hash_session_token(raw)
            conn = get_db()
            try:
                row = conn.execute(
                    """SELECT s.session_id, s.user_id, s.csrf_token, s.created_at,
                              s.expires_at, s.revoked_at, s.last_seen_at,
                              u.email, u.first_name, u.last_name, u.role,
                              u.is_active, u.must_change_password, u.contributor_id
                         FROM user_session s
                         JOIN app_user u ON u.user_id = s.user_id
                        WHERE s.token_hash = ?""",
                    (token_hash,),
                ).fetchone()
                if not row:
                    return None, None
                data = dict(row)
                now = utcnow()

                if data["revoked_at"]:
                    return None, None
                # Deactivating a user takes effect immediately, not at next login.
                if not data["is_active"]:
                    return None, None
                expires = parse_iso(data["expires_at"])
                if expires and now > expires:
                    return None, None
                created = parse_iso(data["created_at"])
                if created and (now - created).days >= auth.ABSOLUTE_SESSION_DAYS:
                    return None, None

                # Slide the idle window forward, but avoid a write on every
                # request — once a minute is plenty.
                last_seen = parse_iso(data["last_seen_at"])
                if not last_seen or (now - last_seen).total_seconds() > 60:
                    prefs = get_preferences(conn)
                    hours = float(prefs.get("idle_timeout_hours",
                                            auth.IDLE_TIMEOUT_HOURS_DEFAULT) or 8)
                    new_expiry = now + datetime.timedelta(hours=hours)
                    conn.execute(
                        "UPDATE user_session SET last_seen_at=?, expires_at=? "
                        "WHERE session_id=?",
                        (iso(now), iso(new_expiry), data["session_id"]))
                    conn.commit()

                user = {
                    "user_id": data["user_id"], "email": data["email"],
                    "first_name": data["first_name"], "last_name": data["last_name"],
                    "role": data["role"], "is_active": data["is_active"],
                    "must_change_password": data["must_change_password"],
                    "contributor_id": data["contributor_id"],
                }
                return user, data
            finally:
                conn.close()

        def clear_session_cookie(self):
            self.clear_cookie(auth.SESSION_COOKIE, path="/")

        def set_session_cookie(self, raw_token, expires_days=None):
            self.set_cookie(
                auth.SESSION_COOKIE, raw_token, path="/", httponly=True,
                secure=self.is_secure_request(), samesite="Strict",
                expires_days=expires_days,
            )

        # -- gatekeeping -----------------------------------------------------
        def prepare(self):
            if not self.requires_auth:
                return

            self.current_user, self.current_session = self.load_session()
            if not self.current_user:
                self.clear_session_cookie()
                return self.fail(401, "Not signed in")

            method = self.request.method

            # CSRF: browsers will not attach a custom header cross-origin, and
            # the token must match the one bound to this session.
            if method not in self.SAFE_METHODS:
                sent = self.request.headers.get(auth.CSRF_HEADER, "")
                expected = (self.current_session or {}).get("csrf_token", "")
                if not expected or sent != expected:
                    return self.fail(403, "Invalid or missing CSRF token")

            # A user with a forced password change can only reach the endpoints
            # needed to change it.
            if (self.current_user.get("must_change_password")
                    and not self.allow_when_password_expired):
                return self.fail(403, "Password change required",
                                 must_change_password=True)

            required = self.READ_ROLE if method in self.SAFE_METHODS else self.WRITE_ROLE
            if not permissions.at_least(self.current_user["role"], required):
                self.record_permission_denied(required)
                return self.fail(
                    403,
                    f"Your role ({self.current_user['role']}) does not permit this "
                    f"action. Requires {required} or higher.")

        def record_permission_denied(self, required):
            conn = get_db()
            try:
                audit.record_event(
                    conn, self.current_user, audit.PERMISSION_DENIED,
                    summary=f"{self.request.method} {self.request.path} "
                            f"requires {required}",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

        # -- auditing helpers -------------------------------------------------
        def audit_create(self, conn, entity_type, entity_id, values):
            return audit.record_create(
                conn, self.current_user, entity_type, entity_id, values,
                ip_address=self.client_ip(), user_agent=self.user_agent())

        def audit_update(self, conn, entity_type, entity_id, before, after,
                         fields=None):
            return audit.record_update(
                conn, self.current_user, entity_type, entity_id, before, after,
                fields=fields, ip_address=self.client_ip(),
                user_agent=self.user_agent())

        def audit_delete(self, conn, entity_type, entity_id, before):
            return audit.record_delete(
                conn, self.current_user, entity_type, entity_id, before,
                ip_address=self.client_ip(), user_agent=self.user_agent())

        def stamp_author(self, conn, table, pk_column, pk_value, creating=False):
            """Record who last touched a row (and who created it)."""
            uid = (self.current_user or {}).get("user_id")
            if uid is None:
                return
            if creating:
                conn.execute(
                    f"UPDATE {table} SET created_by=?, updated_by=? WHERE {pk_column}=?",
                    (uid, uid, pk_value))
            else:
                conn.execute(
                    f"UPDATE {table} SET updated_by=? WHERE {pk_column}=?",
                    (uid, pk_value))

    # -- Systems --
    class SystemsHandler(BaseAPIHandler):
        def get(self):
            conn = get_db()
            rows = conn.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM schema_table st
                           WHERE st.system_id = s.system_id) AS schema_table_count
                   FROM system s ORDER BY s.system_id""").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

        def post(self):
            data = self.get_json_body()
            conn = get_db()
            # Auto-generate ID
            last = conn.execute("SELECT system_id FROM system ORDER BY system_id DESC LIMIT 1").fetchone()
            if last:
                num = int(re.search(r'\d+', last['system_id']).group()) + 1
            else:
                num = 1
            sid = f"SYS-{num:03d}"
            now = datetime.datetime.now().isoformat()
            conn.execute("""INSERT INTO system (system_id,name,description,system_type,vendor,technology,
                environment,managed_by,owner_id,organization,contains_pii,ferpa_protected,status,notes,
                x_pos,y_pos,
                data_classification,access_method,encryption_at_rest,encryption_in_transit,
                last_security_review,has_audit_trail,
                data_steward_id,retention_schedule,destruction_method,backup_frequency,
                disaster_recovery,data_standards,
                authorized_users,data_sharing_restrictions,
                created_date,updated_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, data.get('name',''), data.get('description',''), data.get('system_type','Internal'),
                 data.get('vendor',''), data.get('technology',''), data.get('environment','Production'),
                 data.get('managed_by',''), data.get('owner_id'), data.get('organization',''),
                 data.get('contains_pii',0), data.get('ferpa_protected',0), data.get('status','Active'),
                 data.get('notes',''), data.get('x_pos',400), data.get('y_pos',300),
                 data.get('data_classification'), data.get('access_method'),
                 data.get('encryption_at_rest'), data.get('encryption_in_transit'),
                 data.get('last_security_review'), data.get('has_audit_trail',0),
                 data.get('data_steward_id'), data.get('retention_schedule'),
                 data.get('destruction_method'), data.get('backup_frequency'),
                 data.get('disaster_recovery'), data.get('data_standards'),
                 data.get('authorized_users'), data.get('data_sharing_restrictions'),
                 now, now))
            self.stamp_author(conn, "system", "system_id", sid, creating=True)
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            self.audit_create(conn, "system", sid, dict(row))
            conn.commit()
            conn.close()
            self.set_status(201)
            self.write_json(row_to_dict(row))

    class SystemHandler(BaseAPIHandler):
        def get(self, sid):
            conn = get_db()
            row = conn.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM schema_table st
                           WHERE st.system_id = s.system_id) AS schema_table_count
                   FROM system s WHERE s.system_id=?""", (sid,)).fetchone()
            conn.close()
            if row:
                self.write_json(row_to_dict(row))
            else:
                self.set_status(404)
                self.write_json({"error": "Not found"})

        SYSTEM_FIELDS = ('name','description','system_type','vendor','technology','environment',
                        'managed_by','owner_id','organization','contains_pii','ferpa_protected',
                        'status','notes','x_pos','y_pos',
                        'data_classification','access_method','encryption_at_rest','encryption_in_transit',
                        'last_security_review','has_audit_trail',
                        'data_steward_id','retention_schedule','destruction_method','backup_frequency',
                        'disaster_recovery','data_standards',
                        'authorized_users','data_sharing_restrictions')

        def put(self, sid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            before = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            fields = []
            vals = []
            for key in self.SYSTEM_FIELDS:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(data[key])
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(sid)
                conn.execute(f"UPDATE system SET {','.join(fields)} WHERE system_id=?", vals)
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            # Position changes are excluded from the audit: dragging a node is a
            # layout preference, and logging it would bury real governance edits.
            self.audit_update(conn, "system", sid, before, dict(row),
                              fields=[f for f in self.SYSTEM_FIELDS
                                      if f not in ("x_pos", "y_pos")])
            conn.commit()
            conn.close()
            self.write_json(row_to_dict(row))

        def delete(self, sid):
            conn = get_db()
            before = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            flows = conn.execute(
                "SELECT COUNT(*) FROM data_flow WHERE source_system_id=? "
                "OR destination_system_id=?", (sid, sid)).fetchone()[0]
            if flows:
                conn.close()
                return self.fail(409, f"This system is used by {flows} data flow(s). "
                                      f"Delete or repoint those flows first.")
            conn.execute("DELETE FROM schema_column WHERE table_id IN "
                         "(SELECT table_id FROM schema_table WHERE system_id=?)", (sid,))
            conn.execute("DELETE FROM schema_table WHERE system_id=?", (sid,))
            conn.execute("DELETE FROM system WHERE system_id=?", (sid,))
            self.audit_delete(conn, "system", sid, dict(before))
            conn.commit()
            conn.close()
            self.write_json({"deleted": sid})

    # -- Data Flows --
    class FlowsHandler(BaseAPIHandler):
        def get(self):
            conn = get_db()
            rows = conn.execute("""SELECT f.*, s1.name as source_name, s2.name as dest_name
                FROM data_flow f
                JOIN system s1 ON f.source_system_id=s1.system_id
                JOIN system s2 ON f.destination_system_id=s2.system_id
                ORDER BY f.flow_id""").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

        def post(self):
            data = self.get_json_body()
            conn = get_db()
            last = conn.execute("SELECT flow_id FROM data_flow ORDER BY flow_id DESC LIMIT 1").fetchone()
            if last:
                num = int(re.search(r'\d+', last['flow_id']).group()) + 1
            else:
                num = 1
            fid = f"PL-{num:03d}"
            now = datetime.datetime.now().isoformat()
            conn.execute("""INSERT INTO data_flow (flow_id,name,description,source_system_id,
                destination_system_id,direction,frequency,transfer_method,format,transformation_steps,
                business_rules,validations,error_handling,contains_pii,ferpa_protected,regulatory_driver,
                agreement_id,owner_id,status,known_issues,
                data_classification,encryption_method,disclosure_avoidance,
                data_quality_measures,data_standard,retention_at_destination,legal_authority,
                created_date,updated_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, data.get('name',''), data.get('description',''),
                 data.get('source_system_id',''), data.get('destination_system_id',''),
                 data.get('direction','Inbound'), data.get('frequency','Daily'),
                 data.get('transfer_method',''), data.get('format',''),
                 data.get('transformation_steps',''), data.get('business_rules',''),
                 data.get('validations',''), data.get('error_handling',''),
                 data.get('contains_pii',0), data.get('ferpa_protected',0),
                 data.get('regulatory_driver',''), data.get('agreement_id'),
                 data.get('owner_id'), data.get('status','Active'),
                 data.get('known_issues',''),
                 data.get('data_classification'), data.get('encryption_method'),
                 data.get('disclosure_avoidance'),
                 data.get('data_quality_measures'), data.get('data_standard'),
                 data.get('retention_at_destination'), data.get('legal_authority'),
                 now, now))
            self.stamp_author(conn, "data_flow", "flow_id", fid, creating=True)
            row = conn.execute("SELECT * FROM data_flow WHERE flow_id=?", (fid,)).fetchone()
            self.audit_create(conn, "data_flow", fid, dict(row))
            conn.commit()
            conn.close()
            self.set_status(201)
            self.write_json({"flow_id": fid})

    class FlowHandler(BaseAPIHandler):
        def get(self, fid):
            conn = get_db()
            row = conn.execute("""SELECT f.*, s1.name as source_name, s2.name as dest_name
                FROM data_flow f
                JOIN system s1 ON f.source_system_id=s1.system_id
                JOIN system s2 ON f.destination_system_id=s2.system_id
                WHERE f.flow_id=?""", (fid,)).fetchone()
            elements = conn.execute("""SELECT e.*, fe.is_transformed, fe.transformation_note
                FROM flow_element fe JOIN data_element e ON fe.element_id=e.element_id
                WHERE fe.flow_id=? ORDER BY e.element_id""", (fid,)).fetchall()
            conn.close()
            if row:
                result = row_to_dict(row)
                result['elements'] = rows_to_list(elements)
                self.write_json(result)
            else:
                self.set_status(404)
                self.write_json({"error": "Not found"})

        FLOW_FIELDS = ('name','description','source_system_id','destination_system_id','direction',
                       'frequency','transfer_method','format','transformation_steps','business_rules',
                       'validations','error_handling','contains_pii','ferpa_protected','regulatory_driver',
                       'agreement_id','owner_id','status','known_issues',
                       'data_classification','encryption_method','disclosure_avoidance',
                       'data_quality_measures','data_standard','retention_at_destination','legal_authority')

        def put(self, fid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            before = conn.execute("SELECT * FROM data_flow WHERE flow_id=?", (fid,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            fields = []
            vals = []
            for key in self.FLOW_FIELDS:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(data[key])
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(fid)
                conn.execute(f"UPDATE data_flow SET {','.join(fields)} WHERE flow_id=?", vals)
            after_row = conn.execute("SELECT * FROM data_flow WHERE flow_id=?", (fid,)).fetchone()
            self.audit_update(conn, "data_flow", fid, before, dict(after_row),
                              fields=self.FLOW_FIELDS)
            conn.commit()
            # Re-read the full flow with joins
            row = conn.execute("""SELECT f.*, s1.name as source_name, s2.name as dest_name
                FROM data_flow f
                JOIN system s1 ON f.source_system_id=s1.system_id
                JOIN system s2 ON f.destination_system_id=s2.system_id
                WHERE f.flow_id=?""", (fid,)).fetchone()
            elements = conn.execute("""SELECT e.*, fe.is_transformed, fe.transformation_note
                FROM flow_element fe JOIN data_element e ON fe.element_id=e.element_id
                WHERE fe.flow_id=? ORDER BY e.element_id""", (fid,)).fetchall()
            conn.close()
            if row:
                result = row_to_dict(row)
                result['elements'] = rows_to_list(elements)
                self.write_json(result)
            else:
                self.set_status(404)
                self.write_json({"error": "Not found"})

    # -- Data Elements --
    class ElementsHandler(BaseAPIHandler):
        def get(self):
            conn = get_db()
            rows = conn.execute("SELECT * FROM data_element ORDER BY element_id").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

    class ElementHandler(BaseAPIHandler):
        def get(self, eid):
            conn = get_db()
            row = conn.execute("SELECT * FROM data_element WHERE element_id=?", (eid,)).fetchone()
            flows = conn.execute("""SELECT f.flow_id, f.name, fe.is_transformed, fe.transformation_note
                FROM flow_element fe JOIN data_flow f ON fe.flow_id=f.flow_id
                WHERE fe.element_id=? ORDER BY f.flow_id""", (eid,)).fetchall()
            conn.close()
            if row:
                result = row_to_dict(row)
                result['flows'] = rows_to_list(flows)
                self.write_json(result)
            else:
                self.set_status(404)
                self.write_json({"error": "Not found"})

        ELEMENT_FIELDS = ('name','aliases','definition','data_type','valid_values',
                          'is_pii','ferpa_protected','source_of_truth','notes',
                          'data_classification','data_standard','business_meaning',
                          'data_owner_id','data_steward_id')

        def put(self, eid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            before = conn.execute("SELECT * FROM data_element WHERE element_id=?",
                                  (eid,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            fields = []
            vals = []
            for key in self.ELEMENT_FIELDS:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(data[key])
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(eid)
                conn.execute(f"UPDATE data_element SET {','.join(fields)} WHERE element_id=?", vals)
            after_row = conn.execute("SELECT * FROM data_element WHERE element_id=?",
                                     (eid,)).fetchone()
            self.audit_update(conn, "data_element", eid, before, dict(after_row),
                              fields=self.ELEMENT_FIELDS)
            conn.commit()
            row = conn.execute("SELECT * FROM data_element WHERE element_id=?", (eid,)).fetchone()
            flows = conn.execute("""SELECT f.flow_id, f.name, fe.is_transformed, fe.transformation_note
                FROM flow_element fe JOIN data_flow f ON fe.flow_id=f.flow_id
                WHERE fe.element_id=? ORDER BY f.flow_id""", (eid,)).fetchall()
            conn.close()
            if row:
                result = row_to_dict(row)
                result['flows'] = rows_to_list(flows)
                self.write_json(result)
            else:
                self.set_status(404)
                self.write_json({"error": "Not found"})

    # -- Contributors (formerly "stakeholders") --
    CONTRIBUTOR_FIELDS = (
        'name', 'title', 'organization_unit', 'email', 'phone', 'role_type',
        'notes', 'governance_role', 'governance_responsibilities',
        'training_status', 'last_training_date',
    )

    class ContributorsHandler(BaseAPIHandler):
        def get(self):
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM contributor ORDER BY contributor_id").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

        def post(self):
            data = self.get_json_body()
            if not (data.get('name') or '').strip():
                return self.fail(400, "Name is required")
            conn = get_db()
            try:
                last = conn.execute(
                    "SELECT contributor_id FROM contributor "
                    "ORDER BY contributor_id DESC LIMIT 1").fetchone()
                num = int(re.search(r'\d+', last['contributor_id']).group()) + 1 if last else 1
                cid = f"CON-{num:03d}"
                now = iso(utcnow())
                values = {f: data.get(f) for f in CONTRIBUTOR_FIELDS}
                values['organization_unit'] = values.get('organization_unit') or 'Unspecified'
                conn.execute(
                    """INSERT INTO contributor
                         (contributor_id, name, title, organization_unit, email, phone,
                          role_type, notes, governance_role, governance_responsibilities,
                          training_status, last_training_date, created_date, updated_date,
                          created_by, updated_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cid, values['name'], values['title'], values['organization_unit'],
                     values['email'], values['phone'], values['role_type'],
                     values['notes'], values['governance_role'],
                     values['governance_responsibilities'], values['training_status'],
                     values['last_training_date'], now, now,
                     self.current_user['user_id'], self.current_user['user_id']))
                self.audit_create(conn, "contributor", cid, values)
                conn.commit()
                row = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                                   (cid,)).fetchone()
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(row_to_dict(row))

    class ContributorHandler(BaseAPIHandler):
        def get(self, cid):
            conn = get_db()
            row = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                               (cid,)).fetchone()
            conn.close()
            if not row:
                return self.fail(404, "Not found")
            self.write_json(row_to_dict(row))

        def put(self, cid):
            data = self.get_json_body()
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                                      (cid,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                before = dict(before)
                fields, vals = [], []
                for key in CONTRIBUTOR_FIELDS:
                    if key in data:
                        fields.append(f"{key}=?")
                        vals.append(data[key])
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), self.current_user['user_id'], cid]
                    conn.execute(
                        f"UPDATE contributor SET {','.join(fields)} WHERE contributor_id=?",
                        vals)
                after = dict(conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                                          (cid,)).fetchone())
                self.audit_update(conn, "contributor", cid, before, after,
                                  fields=CONTRIBUTOR_FIELDS)
                conn.commit()
            finally:
                conn.close()
            self.write_json(after)

        def delete(self, cid):
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                                      (cid,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                linked = conn.execute(
                    "SELECT COUNT(*) FROM app_user WHERE contributor_id=?", (cid,)
                ).fetchone()[0]
                if linked:
                    return self.fail(409, "This contributor is linked to a user account. "
                                          "Unlink it before deleting.")
                conn.execute("DELETE FROM contributor WHERE contributor_id=?", (cid,))
                self.audit_delete(conn, "contributor", cid, dict(before))
                conn.commit()
            finally:
                conn.close()
            self.write_json({"deleted": cid})

    # -- Agreements --
    class AgreementsHandler(BaseAPIHandler):
        def get(self):
            conn = get_db()
            rows = conn.execute("SELECT * FROM agreement ORDER BY agreement_id").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

    # -- Position update (for dragging nodes) --
    class PositionHandler(BaseAPIHandler):
        def put(self, sid):
            data = self.get_json_body()
            conn = get_db()
            conn.execute("UPDATE system SET x_pos=?, y_pos=? WHERE system_id=?",
                         (data.get('x_pos', 0), data.get('y_pos', 0), sid))
            conn.commit()
            conn.close()
            self.write_json({"ok": True})

    # -- Schema drill-down --
    class SystemSchemaHandler(BaseAPIHandler):
        """All tables + columns for a system's database schema (for the ER view)."""
        def get(self, sid):
            conn = get_db()
            system = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            if not system:
                conn.close()
                self.set_status(404); self.write_json({"error": "Not found"}); return
            tables = conn.execute(
                "SELECT * FROM schema_table WHERE system_id=? ORDER BY schema_name, table_name",
                (sid,)).fetchall()
            cols_by_table = {}
            table_ids = [t["table_id"] for t in tables]
            if table_ids:
                placeholders = ",".join("?" * len(table_ids))
                cur = conn.execute(
                    f"SELECT * FROM schema_column WHERE table_id IN ({placeholders}) ORDER BY ordinal",
                    table_ids)
                for c in cur.fetchall():
                    cols_by_table.setdefault(c["table_id"], []).append(dict(c))
            conn.close()
            out_tables = []
            for t in tables:
                td = dict(t)
                td["columns"] = cols_by_table.get(t["table_id"], [])
                td["column_count"] = len(td["columns"])
                out_tables.append(td)
            self.write_json({
                "system": {"system_id": system["system_id"], "name": system["name"]},
                "tables": out_tables,
            })

    class SchemaTablePositionHandler(BaseAPIHandler):
        def put(self, table_id):
            data = self.get_json_body()
            conn = get_db()
            conn.execute("UPDATE schema_table SET x_pos=?, y_pos=? WHERE table_id=?",
                         (data.get("x_pos", 0), data.get("y_pos", 0), table_id))
            conn.commit()
            conn.close()
            self.write_json({"ok": True})

    class SchemaTableHandler(BaseAPIHandler):
        def _payload(self, conn, table_id):
            t = conn.execute("SELECT * FROM schema_table WHERE table_id=?", (table_id,)).fetchone()
            if not t:
                return None
            td = dict(t)
            system = conn.execute("SELECT * FROM system WHERE system_id=?", (td["system_id"],)).fetchone()
            sysd = dict(system) if system else {}
            columns = conn.execute(
                "SELECT * FROM schema_column WHERE table_id=? ORDER BY ordinal", (table_id,)).fetchall()
            td["columns"] = rows_to_list(columns)
            td["governance"] = resolve_governance(sysd, td, None)
            # What each field would resolve to if the table override were cleared.
            td["inherited"] = resolve_governance(sysd, {}, None)
            td["parent_system"] = {"system_id": sysd.get("system_id"), "name": sysd.get("name")}
            return td

        def get(self, table_id):
            conn = get_db()
            payload = self._payload(conn, table_id)
            conn.close()
            if payload is None:
                self.set_status(404); self.write_json({"error": "Not found"}); return
            self.write_json(payload)

        def put(self, table_id):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            before = conn.execute("SELECT * FROM schema_table WHERE table_id=?",
                                  (table_id,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            fields, vals = [], []
            for key in GOV_WRITABLE + ["description"]:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(_norm(data[key]))
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(table_id)
                conn.execute(f"UPDATE schema_table SET {','.join(fields)} WHERE table_id=?", vals)
            after_row = conn.execute("SELECT * FROM schema_table WHERE table_id=?",
                                     (table_id,)).fetchone()
            self.audit_update(conn, "schema_table", table_id, before, dict(after_row),
                              fields=GOV_WRITABLE + ["description"])
            conn.commit()
            payload = self._payload(conn, table_id)
            conn.close()
            if payload is None:
                self.set_status(404); self.write_json({"error": "Not found"}); return
            self.write_json(payload)

    class SchemaColumnHandler(BaseAPIHandler):
        def _payload(self, conn, column_id):
            c = conn.execute("SELECT * FROM schema_column WHERE column_id=?", (column_id,)).fetchone()
            if not c:
                return None
            cd = dict(c)
            table = conn.execute("SELECT * FROM schema_table WHERE table_id=?", (cd["table_id"],)).fetchone()
            td = dict(table) if table else {}
            system = conn.execute("SELECT * FROM system WHERE system_id=?", (td.get("system_id"),)).fetchone() if td else None
            sysd = dict(system) if system else {}
            cd["governance"] = resolve_governance(sysd, td, cd)
            # What each field would resolve to if the column override were cleared
            # (i.e. inherit from the parent table, which may inherit from the system).
            cd["inherited"] = resolve_governance(sysd, td, None)
            cd["parent_table"] = {"table_id": td.get("table_id"), "schema_name": td.get("schema_name"),
                                  "table_name": td.get("table_name")}
            cd["parent_system"] = {"system_id": sysd.get("system_id"), "name": sysd.get("name")}
            # Phase 3 placeholder: upstream field-level lineage not yet mapped.
            cd["upstream_lineage"] = []
            return cd

        def get(self, column_id):
            conn = get_db()
            payload = self._payload(conn, column_id)
            conn.close()
            if payload is None:
                self.set_status(404); self.write_json({"error": "Not found"}); return
            self.write_json(payload)

        def put(self, column_id):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            before = conn.execute("SELECT * FROM schema_column WHERE column_id=?",
                                  (column_id,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            fields, vals = [], []
            for key in GOV_WRITABLE:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(_norm(data[key]))
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(column_id)
                conn.execute(f"UPDATE schema_column SET {','.join(fields)} WHERE column_id=?", vals)
            after_row = conn.execute("SELECT * FROM schema_column WHERE column_id=?",
                                     (column_id,)).fetchone()
            self.audit_update(conn, "schema_column", column_id, before, dict(after_row),
                              fields=GOV_WRITABLE)
            conn.commit()
            payload = self._payload(conn, column_id)
            conn.close()
            if payload is None:
                self.set_status(404); self.write_json({"error": "Not found"}); return
            self.write_json(payload)

    # -----------------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------------
    _DUMMY_HASH_CACHE = None

    def dummy_hash():
        """A real hash to verify against when an account doesn't exist, so that a
        wrong email and a wrong password take roughly the same time and the login
        form can't be used to discover which addresses have accounts."""
        global _DUMMY_HASH_CACHE
        if _DUMMY_HASH_CACHE is None:
            _DUMMY_HASH_CACHE = auth.hash_password("not-a-real-password-placeholder")
        return _DUMMY_HASH_CACHE

    def public_settings(conn):
        prefs = get_preferences(conn)
        return {
            "app_title": prefs.get("app_title", "DATA LINEAGE TRACKER"),
            "app_subtitle": prefs.get("app_subtitle", ""),
            "password_policy": auth.describe_password_policy(prefs),
        }

    class AuthLoginHandler(BaseAPIHandler):
        requires_auth = False

        async def post(self):
            data = self.get_json_body()
            email = auth.normalize_email(data.get("email"))
            password = data.get("password") or ""
            ip, ua = self.client_ip(), self.user_agent()
            now = utcnow()

            conn = get_db()
            try:
                cutoff = iso(now - datetime.timedelta(minutes=auth.LOCKOUT_MINUTES))
                fails = conn.execute(
                    "SELECT COUNT(*) FROM login_attempt "
                    "WHERE email=? AND succeeded=0 AND attempted_at>=?",
                    (email, cutoff)).fetchone()[0]

                if fails >= auth.LOCKOUT_THRESHOLD:
                    audit.record_event(
                        conn, {"email": email}, audit.LOGIN_FAILED,
                        summary="Rejected: account temporarily locked",
                        ip_address=ip, user_agent=ua)
                    conn.commit()
                    return self.fail(
                        429, f"Too many failed attempts. Try again in "
                             f"{auth.LOCKOUT_MINUTES} minutes.")

                delay = auth.login_delay_seconds(fails)
                if delay:
                    await asyncio.sleep(delay)

                user = conn.execute(
                    "SELECT * FROM app_user WHERE email=?", (email,)).fetchone()
                if user:
                    ok = (user["is_active"]
                          and auth.verify_password(password, user["password_hash"]))
                else:
                    auth.verify_password(password, dummy_hash())  # equalise timing
                    ok = False

                conn.execute(
                    "INSERT INTO login_attempt (email, attempted_at, succeeded, ip_address) "
                    "VALUES (?,?,?,?)", (email, iso(now), 1 if ok else 0, ip))

                if not ok:
                    audit.record_event(
                        conn, {"email": email}, audit.LOGIN_FAILED,
                        summary="Incorrect credentials or inactive account",
                        ip_address=ip, user_agent=ua)
                    conn.commit()
                    return self.fail(401, "Incorrect email or password.")

                prefs = get_preferences(conn)
                hours = float(prefs.get("idle_timeout_hours",
                                        auth.IDLE_TIMEOUT_HOURS_DEFAULT) or 8)
                raw, token_hash = auth.new_session_token()
                csrf = auth.new_csrf_token()
                conn.execute(
                    """INSERT INTO user_session
                         (user_id, token_hash, csrf_token, created_at, last_seen_at,
                          expires_at, ip_address, user_agent)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (user["user_id"], token_hash, csrf, iso(now), iso(now),
                     iso(now + datetime.timedelta(hours=hours)), ip, ua))
                conn.execute("UPDATE app_user SET last_login_at=? WHERE user_id=?",
                             (iso(now), user["user_id"]))
                actor = {"user_id": user["user_id"], "email": user["email"]}
                audit.record_event(conn, actor, audit.LOGIN, "user", user["user_id"],
                                   summary="Signed in", ip_address=ip, user_agent=ua)
                conn.commit()

                self.set_session_cookie(raw)
                self.write_json({
                    "user": {
                        "user_id": user["user_id"], "email": user["email"],
                        "first_name": user["first_name"], "last_name": user["last_name"],
                        "role": user["role"],
                        "must_change_password": user["must_change_password"],
                        "contributor_id": user["contributor_id"],
                    },
                    "csrf_token": csrf,
                    "settings": public_settings(conn),
                })
            finally:
                conn.close()

    class AuthLogoutHandler(BaseAPIHandler):
        allow_when_password_expired = True
        WRITE_ROLE = permissions.VIEWER

        def post(self):
            conn = get_db()
            try:
                if self.current_session:
                    conn.execute("UPDATE user_session SET revoked_at=? WHERE session_id=?",
                                 (iso(utcnow()), self.current_session["session_id"]))
                    audit.record_event(conn, self.current_user, audit.LOGOUT,
                                       summary="Signed out",
                                       ip_address=self.client_ip(),
                                       user_agent=self.user_agent())
                    conn.commit()
            finally:
                conn.close()
            self.clear_session_cookie()
            self.write_json({"ok": True})

    class AuthMeHandler(BaseAPIHandler):
        allow_when_password_expired = True
        READ_ROLE = permissions.VIEWER

        def get(self):
            conn = get_db()
            try:
                settings = public_settings(conn)
            finally:
                conn.close()
            self.write_json({
                "user": self.current_user,
                "csrf_token": (self.current_session or {}).get("csrf_token"),
                "settings": settings,
                "roles": permissions.ROLES,
                "assignable_roles": permissions.assignable_roles(self.current_user["role"]),
            })

    class ChangePasswordHandler(BaseAPIHandler):
        """Any signed-in user may change their own password."""
        allow_when_password_expired = True
        WRITE_ROLE = permissions.VIEWER

        def post(self):
            data = self.get_json_body()
            current = data.get("current_password") or ""
            new = data.get("new_password") or ""
            conn = get_db()
            try:
                row = conn.execute("SELECT * FROM app_user WHERE user_id=?",
                                   (self.current_user["user_id"],)).fetchone()
                if not row or not auth.verify_password(current, row["password_hash"]):
                    return self.fail(400, "Your current password is incorrect.")
                if auth.verify_password(new, row["password_hash"]):
                    return self.fail(400, "The new password must be different from "
                                          "the current one.")
                prefs = get_preferences(conn)
                problems = auth.check_password_policy(
                    new, prefs, email=row["email"],
                    first_name=row["first_name"], last_name=row["last_name"])
                if problems:
                    return self.fail(400, "Password does not meet requirements.",
                                     problems=problems)
                now = iso(utcnow())
                conn.execute(
                    """UPDATE app_user SET password_hash=?, must_change_password=0,
                           password_changed_at=?, updated_date=?, updated_by=?
                       WHERE user_id=?""",
                    (auth.hash_password(new), now, now,
                     self.current_user["user_id"], self.current_user["user_id"]))
                # Changing a password invalidates other sessions.
                conn.execute(
                    "UPDATE user_session SET revoked_at=? WHERE user_id=? "
                    "AND session_id<>? AND revoked_at IS NULL",
                    (now, self.current_user["user_id"],
                     (self.current_session or {}).get("session_id", -1)))
                audit.record_event(conn, self.current_user, audit.PASSWORD_CHANGED,
                                   "user", self.current_user["user_id"],
                                   summary="Password changed",
                                   ip_address=self.client_ip(),
                                   user_agent=self.user_agent())
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True})

    # -----------------------------------------------------------------------
    # User administration
    # -----------------------------------------------------------------------
    USER_PUBLIC_FIELDS = (
        "user_id", "email", "first_name", "last_name", "role", "is_active",
        "must_change_password", "contributor_id", "last_login_at",
        "password_changed_at", "created_date", "updated_date",
    )

    def user_row_public(row):
        d = dict(row)
        return {k: d.get(k) for k in USER_PUBLIC_FIELDS}

    def count_active_superadmins(conn, excluding=None):
        if excluding is None:
            return conn.execute(
                "SELECT COUNT(*) FROM app_user WHERE role=? AND is_active=1",
                (permissions.SUPERADMIN,)).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM app_user WHERE role=? AND is_active=1 AND user_id<>?",
            (permissions.SUPERADMIN, excluding)).fetchone()[0]

    class UsersHandler(BaseAPIHandler):
        READ_ROLE = permissions.ADMINISTRATOR
        WRITE_ROLE = permissions.ADMINISTRATOR

        def get(self):
            conn = get_db()
            rows = conn.execute(
                """SELECT u.*, c.name AS contributor_name
                     FROM app_user u
                     LEFT JOIN contributor c ON c.contributor_id = u.contributor_id
                    ORDER BY u.email""").fetchall()
            conn.close()
            out = []
            for r in rows:
                d = user_row_public(r)
                d["contributor_name"] = r["contributor_name"]
                out.append(d)
            self.write_json(out)

        def post(self):
            data = self.get_json_body()
            actor_role = self.current_user["role"]
            email = auth.normalize_email(data.get("email"))
            role = data.get("role") or permissions.VIEWER
            first = (data.get("first_name") or "").strip()
            last = (data.get("last_name") or "").strip()
            password = data.get("password") or ""

            if not first or not last:
                return self.fail(400, "First and last name are required.")
            if not permissions.is_valid_role(role):
                return self.fail(400, f"Unknown role '{role}'.")
            if not permissions.can_manage_user(actor_role, role):
                return self.fail(403, f"An {actor_role} cannot create a {role} account.")

            conn = get_db()
            try:
                prefs = get_preferences(conn)
                allowed, why = auth.email_domain_allowed(
                    email, prefs.get("allowed_email_domains", ""))
                if not allowed:
                    return self.fail(400, why)
                if conn.execute("SELECT 1 FROM app_user WHERE email=?", (email,)).fetchone():
                    return self.fail(409, "An account with that email already exists.")

                problems = auth.check_password_policy(
                    password, prefs, email=email, first_name=first, last_name=last)
                if problems:
                    return self.fail(400, "Temporary password does not meet requirements.",
                                     problems=problems)

                now = iso(utcnow())
                row = conn.execute(
                    """INSERT INTO app_user
                         (email, first_name, last_name, password_hash, role, is_active,
                          must_change_password, contributor_id, created_date,
                          updated_date, created_by, updated_by)
                       VALUES (?,?,?,?,?,?,1,?,?,?,?,?)
                       RETURNING user_id""",
                    (email, first, last, auth.hash_password(password), role,
                     1 if data.get("is_active", 1) else 0,
                     data.get("contributor_id") or None, now, now,
                     self.current_user["user_id"], self.current_user["user_id"])
                ).fetchone()
                uid = row["user_id"]
                self.audit_create(conn, "user", uid, {
                    "email": email, "first_name": first, "last_name": last,
                    "role": role, "contributor_id": data.get("contributor_id"),
                })
                conn.commit()
                created = conn.execute("SELECT * FROM app_user WHERE user_id=?",
                                       (uid,)).fetchone()
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(user_row_public(created))

    class UserHandler(BaseAPIHandler):
        READ_ROLE = permissions.ADMINISTRATOR
        WRITE_ROLE = permissions.ADMINISTRATOR

        def get(self, uid):
            conn = get_db()
            row = conn.execute("SELECT * FROM app_user WHERE user_id=?", (uid,)).fetchone()
            conn.close()
            if not row:
                return self.fail(404, "Not found")
            self.write_json(user_row_public(row))

        def put(self, uid):
            data = self.get_json_body()
            actor = self.current_user
            actor_role = actor["role"]
            conn = get_db()
            try:
                row = conn.execute("SELECT * FROM app_user WHERE user_id=?",
                                   (uid,)).fetchone()
                if not row:
                    return self.fail(404, "Not found")
                before = dict(row)

                if not permissions.can_manage_user(actor_role, before["role"]):
                    return self.fail(403, f"An {actor_role} cannot modify a "
                                          f"{before['role']} account.")

                new_role = data.get("role", before["role"])
                if not permissions.is_valid_role(new_role):
                    return self.fail(400, f"Unknown role '{new_role}'.")
                if new_role != before["role"] and not permissions.can_manage_user(
                        actor_role, new_role):
                    return self.fail(403, f"An {actor_role} cannot grant the "
                                          f"{new_role} role.")

                new_active = 1 if data.get("is_active", before["is_active"]) else 0

                # Never allow the last active SuperAdmin to be demoted or disabled.
                losing_superadmin = (
                    before["role"] == permissions.SUPERADMIN
                    and (new_role != permissions.SUPERADMIN or not new_active))
                if losing_superadmin and count_active_superadmins(
                        conn, excluding=int(uid)) == 0:
                    return self.fail(
                        409, "This is the last active SuperAdmin. Promote another "
                             "account first.")

                fields, vals = [], []
                for key in ("first_name", "last_name", "contributor_id"):
                    if key in data:
                        fields.append(f"{key}=?")
                        vals.append(data[key] or None)
                if "role" in data:
                    fields.append("role=?")
                    vals.append(new_role)
                if "is_active" in data:
                    fields.append("is_active=?")
                    vals.append(new_active)

                # An administrator may issue a new temporary password.
                if data.get("password"):
                    prefs = get_preferences(conn)
                    problems = auth.check_password_policy(
                        data["password"], prefs, email=before["email"],
                        first_name=before["first_name"], last_name=before["last_name"])
                    if problems:
                        return self.fail(400, "Password does not meet requirements.",
                                         problems=problems)
                    fields += ["password_hash=?", "must_change_password=?"]
                    vals += [auth.hash_password(data["password"]), 1]

                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), actor["user_id"], uid]
                    conn.execute(f"UPDATE app_user SET {','.join(fields)} WHERE user_id=?",
                                 vals)

                # Deactivation or a password reset ends existing sessions at once.
                if (not new_active and before["is_active"]) or data.get("password"):
                    conn.execute(
                        "UPDATE user_session SET revoked_at=? WHERE user_id=? "
                        "AND revoked_at IS NULL", (iso(utcnow()), uid))

                after = dict(conn.execute("SELECT * FROM app_user WHERE user_id=?",
                                          (uid,)).fetchone())
                self.audit_update(conn, "user", uid, before, after,
                                  fields=("email", "first_name", "last_name", "role",
                                          "is_active", "contributor_id",
                                          "must_change_password"))
                conn.commit()
            finally:
                conn.close()
            self.write_json(user_row_public(after))

    class UserSessionsHandler(BaseAPIHandler):
        """List active sessions, and revoke them (force sign-out)."""
        READ_ROLE = permissions.ADMINISTRATOR
        WRITE_ROLE = permissions.ADMINISTRATOR

        def get(self):
            conn = get_db()
            rows = conn.execute(
                """SELECT s.session_id, s.user_id, s.created_at, s.last_seen_at,
                          s.expires_at, s.ip_address, s.user_agent, u.email, u.role
                     FROM user_session s JOIN app_user u ON u.user_id = s.user_id
                    WHERE s.revoked_at IS NULL AND s.expires_at > ?
                    ORDER BY s.last_seen_at DESC""",
                (iso(utcnow()),)).fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

        def delete(self, session_id=None):
            conn = get_db()
            try:
                target = conn.execute(
                    "SELECT s.*, u.email FROM user_session s "
                    "JOIN app_user u ON u.user_id=s.user_id WHERE s.session_id=?",
                    (session_id,)).fetchone()
                if not target:
                    return self.fail(404, "Session not found")
                conn.execute("UPDATE user_session SET revoked_at=? WHERE session_id=?",
                             (iso(utcnow()), session_id))
                audit.record_event(
                    conn, self.current_user, audit.UPDATE, "session", session_id,
                    summary=f"Revoked session for {target['email']}",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
            finally:
                conn.close()
            self.write_json({"revoked": session_id})

    # -----------------------------------------------------------------------
    # Preferences (SuperAdmin)
    # -----------------------------------------------------------------------
    class PreferencesHandler(BaseAPIHandler):
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def get(self):
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM preference ORDER BY sort_order, key").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

        def put(self):
            data = self.get_json_body() or {}
            updates = data.get("values") or {}
            conn = get_db()
            try:
                existing = {r["key"]: dict(r) for r in
                            conn.execute("SELECT * FROM preference").fetchall()}
                changes = {}
                for key, value in updates.items():
                    pref = existing.get(key)
                    if not pref:
                        continue
                    if not pref["editable"]:
                        return self.fail(400, f"'{key}' is not editable yet.")
                    new_value = "true" if value is True else \
                                "false" if value is False else str(value)
                    if new_value != (pref["value"] or ""):
                        changes[key] = (pref["value"], new_value)
                        conn.execute(
                            "UPDATE preference SET value=?, updated_date=?, updated_by=? "
                            "WHERE key=?",
                            (new_value, iso(utcnow()),
                             self.current_user["user_id"], key))
                if changes:
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE, "preference", None,
                        changes=changes, summary="Preferences updated",
                        ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                invalidate_preferences()
                rows = conn.execute(
                    "SELECT * FROM preference ORDER BY sort_order, key").fetchall()
            finally:
                conn.close()
            self.write_json(rows_to_list(rows))

    # -----------------------------------------------------------------------
    # Audit log (SuperAdmin)
    # -----------------------------------------------------------------------
    class AuditHandler(BaseAPIHandler):
        READ_ROLE = permissions.SUPERADMIN

        def _filters(self):
            return {
                "start": self.get_argument("start", None) or None,
                "end": self.get_argument("end", None) or None,
                "actor_email": self.get_argument("user", None) or None,
                "action": self.get_argument("action", None) or None,
                "entity_type": self.get_argument("entity_type", None) or None,
                "entity_id": self.get_argument("entity_id", None) or None,
            }

        def get(self):
            filters = self._filters()
            limit = min(int(self.get_argument("limit", 100)), 500)
            offset = int(self.get_argument("offset", 0))
            conn = get_db()
            try:
                events, total = audit.query_events(conn, limit=limit, offset=offset,
                                                   **filters)
                actions = [r["action"] for r in conn.execute(
                    "SELECT DISTINCT action FROM audit_event ORDER BY action").fetchall()]
                entities = [r["entity_type"] for r in conn.execute(
                    "SELECT DISTINCT entity_type FROM audit_event "
                    "WHERE entity_type IS NOT NULL ORDER BY entity_type").fetchall()]
            finally:
                conn.close()
            self.write_json({"events": events, "total": total, "limit": limit,
                             "offset": offset, "actions": actions,
                             "entity_types": entities})

    class AuditExportHandler(AuditHandler):
        """CSV of the currently filtered audit view."""
        READ_ROLE = permissions.SUPERADMIN

        def get(self):
            filters = self._filters()
            conn = get_db()
            try:
                events, _ = audit.query_events(conn, limit=100000, offset=0, **filters)
            finally:
                conn.close()

            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Timestamp (UTC)", "User", "Action", "Entity type",
                             "Entity ID", "Field", "Old value", "New value",
                             "Summary", "IP address"])
            for e in events:
                base = [e.get("occurred_at"), e.get("actor_email"), e.get("action"),
                        e.get("entity_type"), e.get("entity_id")]
                tail = [e.get("summary"), e.get("ip_address")]
                changes = e.get("changes") or []
                if not changes:
                    writer.writerow(base + ["", "", ""] + tail)
                for c in changes:
                    writer.writerow(base + [c.get("field_name"), c.get("old_value"),
                                            c.get("new_value")] + tail)

            stamp = utcnow().strftime("%Y%m%d-%H%M%S")
            self.set_header("Content-Type", "text/csv; charset=utf-8")
            self.set_header("Content-Disposition",
                            f'attachment; filename="audit-log-{stamp}.csv"')
            self.finish(buf.getvalue())

    class AppStaticFileHandler(tornado.web.StaticFileHandler):
        """Serve static files, but never let the browser cache index.html.

        The page is a single HTML file containing the whole application, so a
        cached copy means a user keeps running an old build — which is how a
        stale login screen survived a deploy. The HTML is tiny and revalidates
        instantly; everything else may still be cached normally.
        """
        def set_extra_headers(self, path):
            if not path or path.endswith(".html"):
                self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.set_header("Pragma", "no-cache")
                self.set_header("Expires", "0")

    def make_app():
        return tornado.web.Application([
            # Authentication
            (r"/api/auth/login", AuthLoginHandler),
            (r"/api/auth/logout", AuthLogoutHandler),
            (r"/api/auth/me", AuthMeHandler),
            (r"/api/auth/change-password", ChangePasswordHandler),
            # Administration
            (r"/api/users", UsersHandler),
            (r"/api/users/([^/]+)", UserHandler),
            (r"/api/sessions", UserSessionsHandler),
            (r"/api/sessions/([^/]+)", UserSessionsHandler),
            (r"/api/preferences", PreferencesHandler),
            (r"/api/audit/export", AuditExportHandler),
            (r"/api/audit", AuditHandler),
            # Catalog
            (r"/api/systems", SystemsHandler),
            (r"/api/systems/([^/]+)/schema", SystemSchemaHandler),
            (r"/api/systems/([^/]+)/position", PositionHandler),
            (r"/api/systems/([^/]+)", SystemHandler),
            (r"/api/schema/tables/([^/]+)/position", SchemaTablePositionHandler),
            (r"/api/schema/tables/([^/]+)", SchemaTableHandler),
            (r"/api/schema/columns/([^/]+)", SchemaColumnHandler),
            (r"/api/flows", FlowsHandler),
            (r"/api/flows/([^/]+)", FlowHandler),
            (r"/api/elements", ElementsHandler),
            (r"/api/elements/([^/]+)", ElementHandler),
            (r"/api/contributors", ContributorsHandler),
            (r"/api/contributors/([^/]+)", ContributorHandler),
            (r"/api/agreements", AgreementsHandler),
            (r"/(.*)", AppStaticFileHandler, {
                "path": str(STATIC_DIR),
                "default_filename": "index.html"
            }),
        ])

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not HAS_TORNADO:
        sys.exit("Tornado is required. Install dependencies with: pip install -r requirements.txt")

    # Force line-buffered output. Without this, Python block-buffers stdout when
    # it isn't attached to a terminal, so startup messages (including the
    # one-time SuperAdmin password) sit in a buffer instead of reaching the
    # hosting platform's log viewer.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    print("Running database migrations...", flush=True)
    run_migrations()
    seed_db()
    seed_schema()
    ensure_preferences()
    bootstrap_superadmin()

    port = int(os.environ.get("PORT", 8765))

    app = make_app()
    app.listen(port)
    print(f"\n  Data Lineage Tracker running at http://localhost:{port}\n")
    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        close_pool()
