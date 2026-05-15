#!/usr/bin/env python3
"""Data Lineage Tracker — lightweight web application.

Run:  python app.py
Then open http://localhost:8765 in your browser.

Uses Tornado (or falls back to http.server) + SQLite. No other dependencies.
"""

import json, os, sqlite3, datetime, re, sys
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

DB_PATH = Path(__file__).parent / "lineage.db"
STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stakeholder (
    stakeholder_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    title TEXT,
    organization_unit TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    role_type TEXT,
    notes TEXT,
    created_date TEXT NOT NULL DEFAULT (datetime('now')),
    updated_date TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS system (
    system_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    system_type TEXT NOT NULL,
    vendor TEXT,
    technology TEXT,
    environment TEXT,
    managed_by TEXT NOT NULL,
    owner_id TEXT REFERENCES stakeholder(stakeholder_id),
    organization TEXT,
    contains_pii INTEGER NOT NULL DEFAULT 0,
    ferpa_protected INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Active',
    notes TEXT,
    x_pos REAL DEFAULT 0,
    y_pos REAL DEFAULT 0,
    created_date TEXT NOT NULL DEFAULT (datetime('now')),
    updated_date TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agreement (
    agreement_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    partner_agency TEXT NOT NULL,
    agreement_type TEXT NOT NULL,
    legal_basis TEXT,
    data_shared TEXT NOT NULL,
    direction TEXT NOT NULL,
    effective_date TEXT,
    expiration_date TEXT,
    renewal_status TEXT,
    dept_contact_id TEXT REFERENCES stakeholder(stakeholder_id),
    partner_contact TEXT,
    document_link TEXT,
    notes TEXT,
    created_date TEXT NOT NULL DEFAULT (datetime('now')),
    updated_date TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_flow (
    flow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    source_system_id TEXT NOT NULL REFERENCES system(system_id),
    destination_system_id TEXT NOT NULL REFERENCES system(system_id),
    direction TEXT NOT NULL DEFAULT 'Inbound',
    frequency TEXT NOT NULL,
    transfer_method TEXT NOT NULL,
    format TEXT NOT NULL,
    transformation_steps TEXT,
    business_rules TEXT,
    validations TEXT,
    error_handling TEXT,
    contains_pii INTEGER NOT NULL DEFAULT 0,
    ferpa_protected INTEGER NOT NULL DEFAULT 0,
    regulatory_driver TEXT,
    agreement_id TEXT REFERENCES agreement(agreement_id),
    owner_id TEXT REFERENCES stakeholder(stakeholder_id),
    status TEXT NOT NULL DEFAULT 'Active',
    known_issues TEXT,
    created_date TEXT NOT NULL DEFAULT (datetime('now')),
    updated_date TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_element (
    element_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT,
    definition TEXT NOT NULL,
    data_type TEXT,
    valid_values TEXT,
    is_pii INTEGER NOT NULL DEFAULT 0,
    ferpa_protected INTEGER NOT NULL DEFAULT 0,
    source_of_truth TEXT REFERENCES system(system_id),
    notes TEXT,
    created_date TEXT NOT NULL DEFAULT (datetime('now')),
    updated_date TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flow_element (
    flow_id TEXT NOT NULL REFERENCES data_flow(flow_id),
    element_id TEXT NOT NULL REFERENCES data_element(element_id),
    is_transformed INTEGER NOT NULL DEFAULT 0,
    transformation_note TEXT,
    PRIMARY KEY (flow_id, element_id)
);
"""

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
    conn.execute("""INSERT INTO stakeholder VALUES
        ('STK-001','Data Integration Manager','Manager, Data Integration','Data Management Division',
         NULL,NULL,'Data Owner','Primary contact for SIF/CONNECT data pipeline',?,?)""", (now, now))
    conn.execute("""INSERT INTO stakeholder VALUES
        ('STK-002','ODS Administrator','Database Administrator','IT Infrastructure',
         NULL,NULL,'System Admin','Manages the Operational Data Store infrastructure',?,?)""", (now, now))

    # Systems
    systems = [
        ("SYS-001", "District Student Information Systems", "Student Information Systems operated by local school districts (e.g., PowerSchool, Infinite Campus, Tyler SIS). These are the primary source of student demographic, enrollment, and discipline data.", "Source", "Various (PowerSchool, Infinite Campus, Tyler)", "SIS Platforms", "Production", "Local School Districts", "STK-001", "School Districts", 1, 1, "Active", "Multiple vendors across districts; data quality varies by district", 100, 250),
        ("SYS-002", "CONNECT Integration Platform", "SIF-based integration middleware that receives data from district SIS platforms and routes it to the state ODS. Performs initial validation and SIF XML processing.", "ETL Process", "SIF Association / State-developed", "SIF Agent / Middleware", "Production", "Data Management Division", "STK-001", "Department of Education", 1, 1, "Active", "Uses SIF 3.x data model with state-specific extensions", 400, 250),
        ("SYS-003", "State Operational Data Store (ODS)", "Central repository that receives validated data from CONNECT. Stores current-year operational data for all students, schools, and districts.", "Data Store", "State-developed", "SQL Server", "Production", "IT Infrastructure", "STK-002", "Department of Education", 1, 1, "Active", "Primary source of truth for current-year student data", 700, 250),
        ("SYS-004", "Longitudinal Data System (LDS)", "Long-term storage of historical student records spanning multiple years. Fed from the ODS after annual processing and validation.", "Data Store", "State-developed", "SQL Server / Data Warehouse", "Production", "IT Infrastructure", "STK-002", "Department of Education", 1, 1, "Active", "Contains historical records back to initial implementation", 700, 450),
        ("SYS-005", "Federal Reporting (EDFacts)", "Federal reporting system for required compliance submissions including enrollment counts, assessment results, and program participation.", "Destination", "US Department of Education", "EDFacts Submission System (ESS)", "Production", "Data Management Division", "STK-001", "Federal Government", 1, 1, "Active", "Annual submissions required under ESEA/IDEA", 700, 50),
        ("SYS-006", "State Funding Calculations", "System used to calculate state education funding allocations based on enrollment, demographics, and program participation data from the ODS.", "Destination", "State-developed", "Custom Application", "Production", "Finance Division", None, "Department of Education", 1, 1, "Active", "Driven by state funding formula; uses ADM and program counts", 400, 450),
    ]
    for s in systems:
        conn.execute("""INSERT INTO system (system_id,name,description,system_type,vendor,technology,
            environment,managed_by,owner_id,organization,contains_pii,ferpa_protected,status,notes,
            x_pos,y_pos,created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*s, now, now))

    # Data Flows
    flows = [
        ("PL-001", "District SIS to CONNECT (SIF Profile)", "Primary data collection pipeline. Districts transmit student demographic, enrollment, and discipline data to the state via the SIF/CONNECT integration platform. Data conforms to the state SIF 3.x profile with state-specific extensions.", "SYS-001", "SYS-002", "Inbound", "Daily", "SIF Agent (XML over HTTPS)", "SIF 3.x XML",
         "1. District SIS extracts data per SIF profile\n2. SIF Agent packages as SIF XML messages\n3. CONNECT receives and validates against SIF schema\n4. Field-level validation (required fields, data types, code sets)\n5. Cross-reference validation (school IDs, district IDs)\n6. Accepted records queued for ODS loading",
         "State-assigned IDs required (StateProvinceId for students, schools, LEAs)\nSIF extended elements carry state-specific data (ML program dates, special ed fields, truancy tracking)\nOptional fields become conditionally required based on program participation",
         "SIF schema validation on all inbound messages\nRequired field checks per optionality column (M/O/C/MC)\nCharacter length enforcement per SIF profile\nCode set validation against state-defined value lists\nCross-object referential integrity (StudentPersonalRefId, SchoolInfoRefId)",
         "SIF-level errors return error XML response to district agent\nField validation failures logged and flagged for district correction\nRecords with critical errors rejected; non-critical errors accepted with warnings",
         1, 1, "FERPA, State data collection statute", None, "STK-001", "Active",
         "Data quality varies significantly by district and SIS vendor\nSome districts still on older SIF 2.x format requiring translation"),
        ("PL-002", "CONNECT to State ODS", "Validated data from CONNECT loaded into the Operational Data Store. Includes transformation from SIF XML structure to relational database schema.", "SYS-002", "SYS-003", "Inbound", "Daily", "Database ETL (SSIS)", "Relational (SQL Server)",
         "1. Extract validated SIF XML from CONNECT queue\n2. Parse XML into staging tables\n3. Map SIF object hierarchy to flat relational structure\n4. Apply state business rules and derived fields\n5. Deduplication check against existing ODS records\n6. Merge/upsert into ODS production tables",
         "Student deduplication using StateProvinceId as primary match key\nDerived fields calculated from raw data (e.g., age from BirthDate, enrollment duration)\nCode translations from SIF code sets to state reporting codes\nFiscal responsibility determined from ResidencyStatus",
         "Referential integrity checks against ODS master tables\nDuplicate detection across districts\nBusiness rule validation (e.g., enrollment dates within school year)\nData completeness checks for required reporting fields",
         "Staging table holds records pending review\nAutomated retry for transient failures\nManual review queue for business rule violations",
         1, 1, "State data management policy", None, "STK-002", "Active",
         "Deduplication logic needs refinement for students who transfer between districts mid-year"),
        ("PL-003", "ODS to Longitudinal Data System", "Annual load of validated, complete data from the ODS into the long-term longitudinal store.", "SYS-003", "SYS-004", "Outbound", "Annually", "Database ETL (SSIS)", "Relational (SQL Server)",
         "1. End-of-year snapshot from ODS\n2. Final validation and completeness checks\n3. Historical record matching and linking\n4. Load into LDS with school year tagging",
         "Student records linked across years using StateProvinceId\nSchool year attribution for all records\nPoint-in-time snapshot preserves data as of year-end",
         "Completeness checks for all required federal and state reporting fields\nHistorical consistency validation\nYear-over-year enrollment continuity checks",
         "Incomplete records flagged for manual review before LDS load\nMissing data reported to districts for correction",
         1, 1, "State statute, Federal longitudinal data requirements", None, "STK-002", "Active", None),
        ("PL-004", "ODS to Federal Reporting (EDFacts)", "Extract and transform ODS data into required federal reporting formats for EDFacts submission.", "SYS-003", "SYS-005", "Outbound", "Annually", "File Generation + ESS Upload", "Fixed-width / CSV per EDFacts specs",
         "1. Extract relevant data from ODS\n2. Apply federal reporting business rules\n3. Aggregate to required reporting levels\n4. Generate EDFacts file specifications\n5. Validate against ESS edit checks\n6. Submit to EDFacts",
         "Federal aggregation rules differ from state definitions in some cases\nSuppression rules for small cell sizes (FERPA)\nCross-file consistency required across EDFacts submissions",
         "EDFacts edit checks (pre-submission)\nCross-file validation\nYear-over-year comparison for anomaly detection",
         "Edit check failures require data correction and resubmission\nESS provides detailed error reports",
         1, 1, "ESEA, IDEA, FERPA", None, "STK-001", "Active", "Tight federal submission deadlines; requires close coordination"),
        ("PL-005", "ODS to State Funding Calculations", "Enrollment and program participation data extracted from ODS for use in state funding formula calculations.", "SYS-003", "SYS-006", "Outbound", "Annually", "Database view / ETL", "Relational",
         "1. Extract ADM counts from ODS enrollment records\n2. Calculate weighted pupil counts per funding formula\n3. Apply program participation multipliers\n4. Generate funding calculation inputs",
         "Average Daily Membership (ADM) calculation from enrollment records\nWeighted student counts based on program participation (Special Ed, EL, economically disadvantaged)\nFunding formula applies multipliers to base per-pupil amount",
         "ADM counts reconciled against enrollment records\nProgram participation counts validated against eligibility data",
         "Discrepancies flagged for district verification\nAppeals process for disputed counts",
         1, 1, "State funding formula statute", None, "STK-001", "Active", None),
    ]
    for f in flows:
        conn.execute("""INSERT INTO data_flow (flow_id,name,description,source_system_id,destination_system_id,
            direction,frequency,transfer_method,format,transformation_steps,business_rules,validations,
            error_handling,contains_pii,ferpa_protected,regulatory_driver,agreement_id,owner_id,status,
            known_issues,created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*f, now, now))

    # Data Elements (seeded from SIF Profile)
    elements = [
        ("DE-001", "State Student ID (StateProvinceId)", "SASID, SSID, State ID", "Unique student identifier assigned by the state student information system. 9 or 10 characters.", "varchar(10)", "9-10 digit numeric", 1, 1, "SYS-001", "Primary key for student matching across systems; appears in StudentPersonal object"),
        ("DE-002", "Local Student ID (LocalId)", "School Student ID, District ID", "School or district-level student identification number. Up to 25 characters.", "varchar(25)", "Alphanumeric, 0-25 chars", 1, 0, "SYS-001", "District-assigned; may not be unique across districts"),
        ("DE-003", "Student Name (First/Middle/Last)", "FirstName, MiddleName, LastName", "Legal name components of the student including first, middle, last, preferred name, and suffix.", "varchar(50) each", "Alpha, 2-50 chars for first/last", 1, 1, "SYS-001", "No aliases or suffixes in first/last name fields"),
        ("DE-004", "Date of Birth (BirthDate)", "DOB, Birthday", "Student date of birth in ISO format.", "date", "YYYY-MM-DD format", 1, 1, "SYS-001", "Mandatory in StudentPersonal Demographics"),
        ("DE-005", "Gender", None, "Student gender as reported in demographics.", "char(1)", "State-defined code set", 1, 1, "SYS-001", "Mandatory in StudentPersonal Demographics"),
        ("DE-006", "Race/Ethnicity", "RaceList, HispanicLatino", "Student race and Hispanic/Latino indicator. Multiple races may be reported.", "varchar", "Federal race/ethnicity categories", 1, 1, "SYS-001", "Two fields: Race code(s) and Hispanic/Latino indicator; both mandatory"),
        ("DE-007", "Physical Address", "Street, City, State, Zip", "Student physical (residential) address. Address type 0765.", "composite", "Standard US address format", 1, 1, "SYS-001", "PO Box not accepted for physical address; zip code numbers only, no dashes"),
        ("DE-008", "Mailing Address", "Mail Address", "Student mailing address if different from physical. Address type 0123.", "composite", "Standard US address format", 1, 0, "SYS-001", "Optional; defaults to physical if not provided"),
        ("DE-009", "LEA StateProvinceId", "SAU ID, District ID, LEA ID", "NEO Organization ID for the attending School Administrative Unit. 1-4 characters.", "varchar(4)", "1-4 digit numeric", 0, 0, "SYS-001", "Links student to their LEA/SAU; from LEAInfo object"),
        ("DE-010", "School StateProvinceId", "School ID, School Code", "State-assigned school identifier. Used in SchoolInfo object.", "varchar(4)", "1-4 digit numeric", 0, 0, "SYS-001", "Links enrollment to specific school"),
        ("DE-011", "School Name", None, "Official name of the school.", "varchar", "Free text", 0, 0, "SYS-001", "From SchoolInfo object"),
        ("DE-012", "Enrollment Entry Date", "EntryDate, Start Date", "Date student enrolled at the school.", "date", "YYYY-MM-DD", 1, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment"),
        ("DE-013", "Enrollment Entry Type", "EntryType, Entry Code", "Code indicating the type of enrollment entry. Includes state-specific OtherCodeList.", "varchar(5)", "State-defined 5-digit code set", 0, 1, "SYS-001", "Uses OtherCodeList with StateProvince codeset"),
        ("DE-014", "Grade Level", "GradeLevel, Grade", "Student grade level at the enrolled school.", "varchar(2)", "State-defined grade codes", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment"),
        ("DE-015", "Enrollment Exit Date", "ExitDate, End Date, Withdrawal Date", "Date student exited/withdrew from the school.", "date", "YYYY-MM-DD or empty", 1, 1, "SYS-001", "Conditional: required when student exits"),
        ("DE-016", "Enrollment Exit Type", "ExitType, Exit Code, Withdrawal Code", "Code indicating the reason for enrollment exit.", "varchar(5)", "State-defined 5-digit code set", 0, 1, "SYS-001", "Uses OtherCodeList with StateProvince codeset"),
        ("DE-017", "Full-Time/Part-Time Status (FTPTStatus)", "FTE Status", "Indicates whether the student is enrolled full-time or part-time.", "char(1)", "0 or 1", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment; legacy SIF2 users can use extended element"),
        ("DE-018", "Residency/Fiscal Responsibility", "ResidencyStatus", "Indicates the student fiscal responsibility status.", "char(1)", "R and other state codes", 0, 1, "SYS-001", "Uses OtherCodeList; determines funding responsibility"),
        ("DE-019", "Special Education Status", "SPED, IEP, SpecialEd fields", "Collection of special education fields: start date, exit date, exit reason, setting, disability code, and alternate assessment flag.", "composite", "Various per field", 1, 1, "SYS-001", "Six related SIF_ExtendedElements; conditional on student having IEP"),
        ("DE-020", "Economic Disadvantage Status", "EconomicStatus, FRL, Free/Reduced Lunch", "Student economic disadvantage eligibility status including type, start date, and end date.", "composite", "State-defined eligibility codes", 1, 1, "SYS-001", "Three related SIF_ExtendedElements; conditional on eligibility"),
        ("DE-021", "English Learner / ML Program", "EL, ML, ELL, Bilingual, ESL", "Multilingual Learner program participation fields including ML start date, first US school entry date, native language(s), interpreter needs, and specific program types.", "composite", "Various per field", 1, 1, "SYS-001", "Multiple SIF_ExtendedElements covering 5 language slots; program types include Transitional Bilingual, Dual Language, ESL/ELD, Content Classes, Newcomer"),
        ("DE-022", "Homeless Status", "McKinney-Vento, Homeless", "Homeless indicators including nighttime residence code and unaccompanied youth status.", "composite", "State-defined homeless codes", 1, 1, "SYS-001", "Two SIF_ExtendedElements; conditional on student being identified as homeless"),
        ("DE-023", "Title I-A Eligibility", "Title1A, Title I", "Title I-A eligibility flags for Math and ELA.", "char(1) each", "NULL/N/Y", 1, 1, "SYS-001", "Two separate flags: Title1AFlagMath and Title1AFlagELA"),
        ("DE-024", "Military Connected Student", "MilitaryConnected", "Indicates if student has parent(s) in uniformed services.", "varchar", "ActiveDuty, NationalGuard, etc.", 0, 1, "SYS-001", "Mandatory in StudentSchoolEnrollment; based on annual parent survey"),
        ("DE-025", "Discipline Incident", "Incident, Behavior", "Discipline incident record including incident number, date, category, weapon type, offender info, actions taken, duration, and description.", "composite", "Various per field", 1, 1, "SYS-001", "From DisciplineIncident SIF object; links to students via OffenderList"),
        ("DE-026", "Truancy Data", "Truancy, Attendance", "Truancy tracking fields including type, superintendent notification date, intervention plan date, parent notification, board notification, parent meeting, law enforcement referral, and additional interventions.", "composite", "Various per field", 1, 1, "SYS-001", "Eight SIF_ExtendedElements on StudentSchoolEnrollment; conditional on incident category 1240"),
        ("DE-027", "Section 504 Status", "504, Chapter504", "Indicates if student participates in a Chapter 504 program.", "char(1)", "NULL/N/Y", 1, 1, "SYS-001", "Conditional SIF_ExtendedElement"),
        ("DE-028", "Gifted and Talented", "GT, Gifted", "Indicates student participation in Gifted and Talented programs, both Academic and Artistic.", "char(1) each", "NULL/N/Y", 0, 1, "SYS-001", "Two separate flags: GiftedTalentedAcademic and GiftedTalentedArtistic"),
        ("DE-029", "Resident Town (GeoCode)", "ResidentTown, TownCode", "ID of the town the student resides in.", "varchar(5)", "5-digit town code", 0, 0, "SYS-001", "Mandatory in StudentSchoolEnrollment; uses state geo code lookup"),
        ("DE-030", "Parent/Guardian Names", "Parent, Guardian", "First and last names of up to two parents or guardians.", "varchar(50) each", "Alpha, up to 50 chars", 1, 1, "SYS-001", "Four SIF_ExtendedElements; ampersand not accepted; first/last are paired (one requires the other)"),
        ("DE-031", "Immigrant Status", "Immigrant", "Indicates whether the student is an immigrant.", "char(1)", "NULL/N/Y", 1, 1, "SYS-001", "Optional field on StudentPersonal"),
        ("DE-032", "Home Instruction Flag", "HomeInstruction, Home School", "Indicates students who are home instructed and attend local school part time or receive only Special Education services.", "char(1)", "NULL/N/Y", 0, 1, "SYS-001", "If yes, FTE value may be required"),
        ("DE-033", "Membership Type", "MembershipType, Primary/Secondary", "Indicates whether the enrollment is the student primary or secondary enrollment.", "char(1)", "P or S", 0, 1, "SYS-001", "Attribute on StudentSchoolEnrollment object"),
    ]
    for e in elements:
        conn.execute("""INSERT INTO data_element (element_id,name,aliases,definition,data_type,
            valid_values,is_pii,ferpa_protected,source_of_truth,notes,created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (*e, now, now))

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
    print(f"Database seeded at {DB_PATH}")

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
            rows = conn.execute("SELECT * FROM system ORDER BY system_id").fetchall()
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
                x_pos,y_pos,created_date,updated_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, data.get('name',''), data.get('description',''), data.get('system_type','Internal'),
                 data.get('vendor',''), data.get('technology',''), data.get('environment','Production'),
                 data.get('managed_by',''), data.get('owner_id'), data.get('organization',''),
                 data.get('contains_pii',0), data.get('ferpa_protected',0), data.get('status','Active'),
                 data.get('notes',''), data.get('x_pos',400), data.get('y_pos',300), now, now))
            conn.commit()
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            conn.close()
            self.set_status(201)
            self.write_json(row_to_dict(row))

    class SystemHandler(BaseAPIHandler):
        def get(self, sid):
            conn = get_db()
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
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
                        'status','notes','x_pos','y_pos'):
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
                agreement_id,owner_id,status,known_issues,created_date,updated_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, data.get('name',''), data.get('description',''),
                 data.get('source_system_id',''), data.get('destination_system_id',''),
                 data.get('direction','Inbound'), data.get('frequency','Daily'),
                 data.get('transfer_method',''), data.get('format',''),
                 data.get('transformation_steps',''), data.get('business_rules',''),
                 data.get('validations',''), data.get('error_handling',''),
                 data.get('contains_pii',0), data.get('ferpa_protected',0),
                 data.get('regulatory_driver',''), data.get('agreement_id'),
                 data.get('owner_id'), data.get('status','Active'),
                 data.get('known_issues',''), now, now))
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
                        'agreement_id','owner_id','status','known_issues'):
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
                        'is_pii','ferpa_protected','source_of_truth','notes'):
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

    def make_app():
        return tornado.web.Application([
            (r"/api/systems", SystemsHandler),
            (r"/api/systems/([^/]+)", SystemHandler),
            (r"/api/systems/([^/]+)/position", PositionHandler),
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
    print("Initializing database...")
    init_db()
    seed_db()

    port = int(os.environ.get("PORT", 8765))

    if HAS_TORNADO:
        app = make_app()
        app.listen(port)
        print(f"\n  Data Lineage Tracker running at http://localhost:{port}\n")
        try:
            tornado.ioloop.IOLoop.current().start()
        except KeyboardInterrupt:
            print("\nShutting down...")
    else:
        import http.server, functools
        print(f"Tornado not available. Serving static files only at http://localhost:{port}")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
        with http.server.HTTPServer(("", port), handler) as httpd:
            httpd.serve_forever()
