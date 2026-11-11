"""Parse a SQL Server (T-SQL) schema dump into tables and columns.

Used by the "Upload schema" action on Data Store systems. The parser is
deliberately narrow: it reads ``CREATE TABLE`` statements and their inline
``PRIMARY KEY`` constraints, plus foreign keys where a dump declares them. It
does not try to be a general SQL parser — it needs to understand the scripts
SQL Server Management Studio produces, and to fail clearly on anything else.

Kept separate from the web layer so it can be tested directly against real
dumps.
"""

import re

# [Schema].[Table]( ... ) ON [PRIMARY]  — the shape SSMS emits.
_CREATE_TABLE = re.compile(
    r'CREATE\s+TABLE\s+\[?(?P<schema>[^\]\.\s]+)\]?\s*\.\s*\[?(?P<table>[^\]\s\(]+)\]?\s*\((?P<body>.*?)\n\)\s*ON\s',
    re.S | re.I)
# Fallback for dumps without the trailing "ON [PRIMARY]" clause.
_CREATE_TABLE_SIMPLE = re.compile(
    r'CREATE\s+TABLE\s+\[?(?P<schema>[^\]\.\s]+)\]?\s*\.\s*\[?(?P<table>[^\]\s\(]+)\]?\s*\((?P<body>.*?)\n\);',
    re.S | re.I)

_COLUMN = re.compile(r'^\s*\[(?P<name>[^\]]+)\]\s+\[(?P<type>[^\]]+)\](?P<rest>.*)$')
_PK_BLOCK = re.compile(r'PRIMARY\s+KEY(?:\s+CLUSTERED|\s+NONCLUSTERED)?\s*\((?P<cols>.*?)\)', re.S | re.I)
_BRACKETED = re.compile(r'\[([^\]]+)\]')

# ALTER TABLE ... FOREIGN KEY ... REFERENCES ...
_FOREIGN_KEY = re.compile(
    r'ALTER\s+TABLE\s+\[?(?P<from_schema>[^\]\.\s]+)\]?\s*\.\s*\[?(?P<from_table>[^\]\s]+)\]?\s+'
    r'(?:WITH\s+(?:NO)?CHECK\s+)?ADD\s+(?:CONSTRAINT\s+\[?(?P<constraint>[^\]\s]+)\]?\s+)?'
    r'FOREIGN\s+KEY\s*\(\s*\[?(?P<from_column>[^\]\)\s]+)\]?\s*\)\s*'
    r'REFERENCES\s+\[?(?P<to_schema>[^\]\.\s]+)\]?\s*\.\s*\[?(?P<to_table>[^\]\s\(]+)\]?\s*'
    r'\(\s*\[?(?P<to_column>[^\]\)\s]+)\]?\s*\)',
    re.S | re.I)


class SchemaParseError(Exception):
    """Raised when a dump contains nothing we can use."""


def _column_type(base, rest):
    """Reassemble a column type, including any length/precision."""
    length = re.match(r'\s*\(([^)]*)\)', rest)
    if length:
        return f"{base}({length.group(1).replace(' ', '')})"
    return base


def parse_tsql(sql_text, per_row=8, col_gap=290, row_gap=360,
               table_prefix="TBL", column_prefix="COL"):
    """Parse a T-SQL dump.

    Returns ``{"tables": [...], "columns": [...], "warnings": [...]}`` in the
    same shape the application stores, with grid positions assigned so the ER
    diagram has a sensible starting layout.
    """
    if not sql_text or not sql_text.strip():
        raise SchemaParseError("The file is empty.")

    blocks = list(_CREATE_TABLE.finditer(sql_text))
    if not blocks:
        blocks = list(_CREATE_TABLE_SIMPLE.finditer(sql_text))
    if not blocks:
        raise SchemaParseError(
            "No CREATE TABLE statements were found. This importer expects a "
            "SQL Server (T-SQL) schema script, of the kind SQL Server "
            "Management Studio generates.")

    warnings = []
    tables, columns = [], []
    table_index = {}

    for i, block in enumerate(blocks):
        schema_name = block.group("schema")
        table_name = block.group("table")
        body = block.group("body")

        pk_cols = []
        pk = _PK_BLOCK.search(body)
        if pk:
            pk_cols = _BRACKETED.findall(pk.group("cols"))

        table_id = f"{table_prefix}-{i + 1:04d}"
        row, col = divmod(i, per_row)
        table_index[(schema_name, table_name)] = table_id
        tables.append({
            "table_id": table_id,
            "schema_name": schema_name,
            "table_name": table_name,
            "primary_key": ", ".join(pk_cols) if pk_cols else None,
            "x_pos": col * col_gap + 40,
            "y_pos": row * row_gap + 40,
        })

        ordinal = 0
        seen_constraint = False
        for line in body.splitlines():
            # Everything from the first constraint onwards is not a column.
            #
            # The keyword search deliberately ignores anything inside square
            # brackets: real dumps contain columns with names like
            # "[Unique DRC Student ID]", and matching the word UNIQUE there
            # would truncate the table and silently drop every column after it.
            outside_identifiers = _BRACKETED.sub("", line)
            if re.search(r'\bCONSTRAINT\b|\bPRIMARY\s+KEY\b|\bUNIQUE\b|\bCHECK\b',
                         outside_identifiers, re.I):
                seen_constraint = True
            if seen_constraint:
                continue
            m = _COLUMN.match(line)
            if not m:
                continue
            rest = m.group("rest")
            ordinal += 1
            columns.append({
                "column_id": f"{column_prefix}-{len(columns) + 1:04d}",
                "table_id": table_id,
                "column_name": m.group("name"),
                "ordinal": ordinal,
                "data_type": _column_type(m.group("type"), rest),
                "is_nullable": 0 if re.search(r'\bNOT\s+NULL\b', rest, re.I) else 1,
                "is_identity": 1 if re.search(r'\bIDENTITY\b', rest, re.I) else 0,
                "is_pk": 1 if m.group("name") in pk_cols else 0,
                "is_fk": 0,
                "default_value": None,
                "fk_target_schema": None,
                "fk_target_table": None,
                "fk_target_column": None,
                "fk_target_table_id": None,
                "fk_constraint": None,
            })

        if ordinal == 0:
            warnings.append(f"No columns were read from {schema_name}.{table_name}.")

    # Foreign keys, where the dump declares them.
    by_name = {}
    for c in columns:
        by_name[(c["table_id"], c["column_name"])] = c

    fk_count = 0
    for fk in _FOREIGN_KEY.finditer(sql_text):
        src = table_index.get((fk.group("from_schema"), fk.group("from_table")))
        if not src:
            continue
        col = by_name.get((src, fk.group("from_column")))
        if not col:
            continue
        col["is_fk"] = 1
        col["fk_target_schema"] = fk.group("to_schema")
        col["fk_target_table"] = fk.group("to_table")
        col["fk_target_column"] = fk.group("to_column")
        col["fk_target_table_id"] = table_index.get(
            (fk.group("to_schema"), fk.group("to_table")))
        col["fk_constraint"] = fk.group("constraint")
        fk_count += 1

    if not columns:
        raise SchemaParseError(
            "Tables were found but no columns could be read. The script may use "
            "a format this importer doesn't recognize.")

    return {
        "tables": tables,
        "columns": columns,
        "warnings": warnings,
        "stats": {
            "tables": len(tables),
            "columns": len(columns),
            "foreign_keys": fk_count,
            "schemas": sorted({t["schema_name"] for t in tables}),
        },
    }


# ---------------------------------------------------------------------------
# Carrying governance forward across an upload
# ---------------------------------------------------------------------------

# Values a user may have set by hand, which must survive a re-upload.
TABLE_GOVERNANCE = [
    "description", "data_classification", "contains_pii", "ferpa_protected",
    "data_owner_id", "data_steward_id", "retention_schedule",
    "access_restrictions", "legal_authority", "business_meaning", "notes",
]
COLUMN_GOVERNANCE = [
    "data_classification", "contains_pii", "ferpa_protected", "data_owner_id",
    "data_steward_id", "retention_schedule", "access_restrictions",
    "legal_authority", "business_meaning", "notes",
]


def carry_governance(parsed, existing_tables, existing_columns):
    """Copy hand-entered governance onto freshly parsed tables and columns.

    Matching is by ``schema.table`` and ``schema.table.column`` name, so an
    unchanged column keeps everything set on it. A renamed column looks like a
    removal plus an addition and starts blank — unavoidable without a manual
    mapping. Node positions are carried across too, so an existing layout isn't
    scrambled by a re-upload.

    Returns a summary of what matched, for the confirmation screen.
    """
    old_tables = {}
    for t in existing_tables:
        old_tables[(t.get("schema_name"), t.get("table_name"))] = t

    old_cols = {}
    table_key_by_id = {t["table_id"]: (t.get("schema_name"), t.get("table_name"))
                       for t in existing_tables}
    for c in existing_columns:
        key = table_key_by_id.get(c.get("table_id"))
        if key:
            old_cols[(key[0], key[1], c.get("column_name"))] = c

    matched_tables = matched_columns = carried = 0

    for t in parsed["tables"]:
        old = old_tables.get((t["schema_name"], t["table_name"]))
        if not old:
            continue
        matched_tables += 1
        # Keep the existing layout position.
        if old.get("x_pos") is not None:
            t["x_pos"] = old["x_pos"]
            t["y_pos"] = old["y_pos"]
        for field in TABLE_GOVERNANCE:
            value = old.get(field)
            if value is not None and value != "":
                t[field] = value
                carried += 1

    table_key_by_new_id = {t["table_id"]: (t["schema_name"], t["table_name"])
                           for t in parsed["tables"]}
    for c in parsed["columns"]:
        key = table_key_by_new_id.get(c["table_id"])
        if not key:
            continue
        old = old_cols.get((key[0], key[1], c["column_name"]))
        if not old:
            continue
        matched_columns += 1
        for field in COLUMN_GOVERNANCE:
            value = old.get(field)
            if value is not None and value != "":
                c[field] = value
                carried += 1

    return {
        "matched_tables": matched_tables,
        "matched_columns": matched_columns,
        "carried_values": carried,
        "new_tables": len(parsed["tables"]) - matched_tables,
        "new_columns": len(parsed["columns"]) - matched_columns,
        "removed_tables": len(existing_tables) - matched_tables,
        "removed_columns": len(existing_columns) - matched_columns,
    }
