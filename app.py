#!/usr/bin/env python3
"""Data Lineage Tracker — lightweight web application.

Run locally with Docker Compose:  docker compose up
Then open http://localhost:8765 in your browser.

Uses Tornado + PostgreSQL. The schema is managed by Alembic migrations
(see alembic/versions/), and database access goes through db.py, which keeps
the sqlite3-style calling convention used throughout this file.
"""

import json, os, datetime, re, sys
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

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_DIR = Path(__file__).parent

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
    conn.execute("""INSERT INTO stakeholder (stakeholder_id,name,title,organization_unit,email,phone,
        role_type,notes,governance_role,governance_responsibilities,training_status,last_training_date,
        created_date,updated_date) VALUES
        ('STK-001','Data Integration Manager','Manager, Data Integration','Data Management Division',
         NULL,NULL,'Data Owner','Primary contact for SIF/CONNECT data pipeline',
         'Data Owner','Oversees data collection policies, approves data sharing agreements, ensures compliance with FERPA and state regulations',
         'Current','2025-09-15',?,?)""", (now, now))
    conn.execute("""INSERT INTO stakeholder (stakeholder_id,name,title,organization_unit,email,phone,
        role_type,notes,governance_role,governance_responsibilities,training_status,last_training_date,
        created_date,updated_date) VALUES
        ('STK-002','ODS Administrator','Database Administrator','IT Infrastructure',
         NULL,NULL,'System Admin','Manages the Operational Data Store infrastructure',
         'Data Steward','Day-to-day maintenance of ODS and LDS, data quality monitoring, backup and recovery operations',
         'Current','2025-10-01',?,?)""", (now, now))

    # Systems (with new governance fields)
    systems = [
        ("SYS-001", "District Student Information Systems",
         "Student Information Systems operated by local school districts (e.g., PowerSchool, Infinite Campus, Tyler SIS). These are the primary source of student demographic, enrollment, and discipline data.",
         "Source", "Various (PowerSchool, Infinite Campus, Tyler)", "SIS Platforms", "Production",
         "Local School Districts", "STK-001", "School Districts", 1, 1, "Active",
         "Multiple vendors across districts; data quality varies by district", 100, 250,
         "Confidential", "Varies by district", "Varies by vendor", "HTTPS/TLS 1.2", None, 0,
         None, "Per district policy", "Per district policy", "Varies", "Varies",
         "SIF 3.x with state extensions", "District staff with SIS access", "FERPA restrictions apply"),
        ("SYS-002", "CONNECT Integration Platform",
         "SIF-based integration middleware that receives data from district SIS platforms and routes it to the state ODS. Performs initial validation and SIF XML processing.",
         "ETL Process", "SIF Association / State-developed", "SIF Agent / Middleware", "Production",
         "Data Management Division", "STK-001", "Department of Education", 1, 1, "Active",
         "Uses SIF 3.x data model with state-specific extensions", 400, 250,
         "Confidential", "Role-based access", "AES-256", "TLS 1.2", "2025-06-15", 1,
         "STK-001", "7 years per state policy", "Secure deletion", "Daily", "Documented",
         "SIF 3.x", "Data Management Division staff", "No external sharing without agreement"),
        ("SYS-003", "NEO (State Operational Data Store)",
         "Central repository that receives validated data from CONNECT. Stores current-year operational data for all students, schools, and districts.",
         "Data Store", "State-developed", "SQL Server", "Production",
         "IT Infrastructure", "STK-002", "Department of Education", 1, 1, "Active",
         "Primary source of truth for current-year student data", 700, 250,
         "Confidential", "Role-based access with AD integration", "TDE (Transparent Data Encryption)", "TLS 1.2", "2025-08-01", 1,
         "STK-002", "7 years per state policy", "Secure deletion with verification", "Daily incremental, weekly full", "Full DR plan with 4-hour RTO",
         "State data model (derived from SIF/CEDS)", "Authorized DOE staff only", "Restricted to authorized personnel per FERPA"),
        ("SYS-004", "Longitudinal Data System (LDS)",
         "Long-term storage of historical student records spanning multiple years. Fed from the ODS after annual processing and validation.",
         "Data Store", "State-developed", "SQL Server / Data Warehouse", "Production",
         "IT Infrastructure", "STK-002", "Department of Education", 1, 1, "Active",
         "Contains historical records back to initial implementation", 700, 450,
         "Confidential", "Role-based access with AD integration", "TDE (Transparent Data Encryption)", "TLS 1.2", "2025-08-01", 1,
         "STK-002", "Permanent retention", "N/A - permanent archive", "Daily incremental, weekly full", "Full DR plan with 4-hour RTO",
         "State longitudinal data model", "Authorized research and reporting staff", "Strict FERPA controls; data sharing requires agreement"),
        ("SYS-005", "Federal Reporting (EDFacts)",
         "Federal reporting system for required compliance submissions including enrollment counts, assessment results, and program participation.",
         "Destination", "US Department of Education", "EDFacts Submission System (ESS)", "Production",
         "Data Management Division", "STK-001", "Federal Government", 1, 1, "Active",
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
         "Data Management Division", "STK-001", "Department of Education", 1, 1, "Active",
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
         1, 1, "FERPA, State data collection statute", None, "STK-001", "Active",
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
         1, 1, "State data management policy", None, "STK-002", "Active",
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
         1, 1, "State statute, Federal longitudinal data requirements", None, "STK-002", "Active", None,
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
         1, 1, "ESEA, IDEA, FERPA", None, "STK-001", "Active", "Tight federal submission deadlines; requires close coordination",
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
         1, 1, "State funding formula statute", None, "STK-001", "Active", None,
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
         1, 1, "ESEA, IDEA, FERPA", None, "STK-001", "Active",
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
         1, 1, "State staff data collection statute, FERPA", None, "STK-001", "Active",
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
         "Confidential", "SIF 3.x / CEDS", "The primary identifier used to uniquely track a student across all state education systems, districts, and reporting periods", "STK-001", "STK-002"),
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
         "Confidential", "SIF 3.x / IDEA", "Special education program participation data required for IDEA compliance reporting", "STK-001", None),
        ("DE-020", "Economic Disadvantage Status", "EconomicStatus, FRL, Free/Reduced Lunch", "Student economic disadvantage eligibility status including type, start date, and end date.", "composite", "State-defined eligibility codes", 1, 1, "SYS-001", "Three related SIF_ExtendedElements; conditional on eligibility",
         "Confidential", "SIF 3.x / State codes", "Economic disadvantage indicators used for Title I eligibility and funding calculations", None, None),
        ("DE-021", "English Learner / ML Program", "EL, ML, ELL, Bilingual, ESL", "Multilingual Learner program participation fields including ML start date, first US school entry date, native language(s), interpreter needs, and specific program types.", "composite", "Various per field", 1, 1, "SYS-001", "Multiple SIF_ExtendedElements covering 5 language slots; program types include Transitional Bilingual, Dual Language, ESL/ELD, Content Classes, Newcomer",
         "Confidential", "SIF 3.x / State codes", "Multilingual learner program participation data for Title III compliance and services tracking", "STK-001", None),
        ("DE-022", "Homeless Status", "McKinney-Vento, Homeless", "Homeless indicators including nighttime residence code and unaccompanied youth status.", "composite", "State-defined homeless codes", 1, 1, "SYS-001", "Two SIF_ExtendedElements; conditional on student being identified as homeless",
         "Confidential", "SIF 3.x / McKinney-Vento", "McKinney-Vento homeless status indicators for federal compliance reporting", None, None),
        ("DE-023", "Title I-A Eligibility", "Title1A, Title I", "Title I-A eligibility flags for Math and ELA.", "char(1) each", "NULL/N/Y", 1, 1, "SYS-001", "Two separate flags: Title1AFlagMath and Title1AFlagELA",
         "Confidential", "SIF 3.x / ESEA", "Title I-A program eligibility for federal funding and reporting", None, None),
        ("DE-024", "Military Connected Student", "MilitaryConnected", "Indicates if student has parent(s) in uniformed services.", "varchar", "ActiveDuty, NationalGuard, etc.", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment; based on annual parent survey",
         "Sensitive", "SIF 3.x / Federal", "Military family connection status for federal impact aid reporting", None, None),
        ("DE-025", "Discipline Incident", "Incident, Behavior", "Discipline incident record including incident number, date, category, weapon type, offender info, actions taken, duration, and description.", "composite", "Various per field", 1, 1, "SYS-001", "From DisciplineIncident SIF object; links to students via OffenderList",
         "Confidential", "SIF 3.x / CRDC", "Discipline event data required for Civil Rights Data Collection and school safety reporting", "STK-001", None),
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
# Tornado handlers
# ---------------------------------------------------------------------------

if HAS_TORNADO:

    class BaseAPIHandler(tornado.web.RequestHandler):
        def set_default_headers(self):
            self.set_header("Content-Type", "application/json")

        def write_json(self, data):
            self.write(json.dumps(data, default=str))

        def get_json_body(self):
            try:
                return json.loads(self.request.body)
            except Exception:
                return {}

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
            conn.commit()
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
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

        def put(self, sid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            fields = []
            vals = []
            for key in ('name','description','system_type','vendor','technology','environment',
                        'managed_by','owner_id','organization','contains_pii','ferpa_protected',
                        'status','notes','x_pos','y_pos',
                        'data_classification','access_method','encryption_at_rest','encryption_in_transit',
                        'last_security_review','has_audit_trail',
                        'data_steward_id','retention_schedule','destruction_method','backup_frequency',
                        'disaster_recovery','data_standards',
                        'authorized_users','data_sharing_restrictions'):
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(data[key])
            if fields:
                fields.append("updated_date=?")
                vals.append(now)
                vals.append(sid)
                conn.execute(f"UPDATE system SET {','.join(fields)} WHERE system_id=?", vals)
                conn.commit()
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            conn.close()
            self.write_json(row_to_dict(row))

        def delete(self, sid):
            conn = get_db()
            conn.execute("DELETE FROM system WHERE system_id=?", (sid,))
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

        def put(self, fid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            fields = []
            vals = []
            for key in ('name','description','source_system_id','destination_system_id','direction',
                        'frequency','transfer_method','format','transformation_steps','business_rules',
                        'validations','error_handling','contains_pii','ferpa_protected','regulatory_driver',
                        'agreement_id','owner_id','status','known_issues',
                        'data_classification','encryption_method','disclosure_avoidance',
                        'data_quality_measures','data_standard','retention_at_destination','legal_authority'):
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(data[key])
            if fields:
                fields.append("updated_date=?")
                vals.append(now)
                vals.append(fid)
                conn.execute(f"UPDATE data_flow SET {','.join(fields)} WHERE flow_id=?", vals)
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

        def put(self, eid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            fields = []
            vals = []
            for key in ('name','aliases','definition','data_type','valid_values',
                        'is_pii','ferpa_protected','source_of_truth','notes',
                        'data_classification','data_standard','business_meaning',
                        'data_owner_id','data_steward_id'):
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(data[key])
            if fields:
                fields.append("updated_date=?")
                vals.append(now)
                vals.append(eid)
                conn.execute(f"UPDATE data_element SET {','.join(fields)} WHERE element_id=?", vals)
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

    # -- Stakeholders --
    class StakeholdersHandler(BaseAPIHandler):
        def get(self):
            conn = get_db()
            rows = conn.execute("SELECT * FROM stakeholder ORDER BY stakeholder_id").fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

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
            fields, vals = [], []
            for key in GOV_WRITABLE + ["description"]:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(_norm(data[key]))
            if fields:
                fields.append("updated_date=?")
                vals.append(now)
                vals.append(table_id)
                conn.execute(f"UPDATE schema_table SET {','.join(fields)} WHERE table_id=?", vals)
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
            fields, vals = [], []
            for key in GOV_WRITABLE:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(_norm(data[key]))
            if fields:
                fields.append("updated_date=?")
                vals.append(now)
                vals.append(column_id)
                conn.execute(f"UPDATE schema_column SET {','.join(fields)} WHERE column_id=?", vals)
                conn.commit()
            payload = self._payload(conn, column_id)
            conn.close()
            if payload is None:
                self.set_status(404); self.write_json({"error": "Not found"}); return
            self.write_json(payload)

    def make_app():
        return tornado.web.Application([
            (r"/api/systems", SystemsHandler),
            (r"/api/systems/([^/]+)/schema", SystemSchemaHandler),
            (r"/api/systems/([^/]+)", SystemHandler),
            (r"/api/systems/([^/]+)/position", PositionHandler),
            (r"/api/schema/tables/([^/]+)/position", SchemaTablePositionHandler),
            (r"/api/schema/tables/([^/]+)", SchemaTableHandler),
            (r"/api/schema/columns/([^/]+)", SchemaColumnHandler),
            (r"/api/flows", FlowsHandler),
            (r"/api/flows/([^/]+)", FlowHandler),
            (r"/api/elements", ElementsHandler),
            (r"/api/elements/([^/]+)", ElementHandler),
            (r"/api/stakeholders", StakeholdersHandler),
            (r"/api/agreements", AgreementsHandler),
            (r"/(.*)", tornado.web.StaticFileHandler, {
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

    print("Running database migrations...")
    run_migrations()
    seed_db()
    seed_schema()

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
