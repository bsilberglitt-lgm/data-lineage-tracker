#!/usr/bin/env python3
"""Data Lineage Tracker — lightweight web application.

Run locally with Docker Compose:  docker compose up
Then open http://localhost:8765 in your browser.

Uses Tornado + PostgreSQL. The schema is managed by Alembic migrations
(see alembic/versions/), and database access goes through db.py, which keeps
the sqlite3-style calling convention used throughout this file.
"""

import asyncio, csv, hashlib, io, json, os, datetime, re, struct, sys, time
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
import schema_import  # noqa: E402
import scenarios  # noqa: E402
import help_articles  # noqa: E402
import mailer  # noqa: E402  (SMTP / Resend; stdlib only)
import field_catalog  # noqa: E402  (generated; see tools/gen_field_catalog.py)
from version import VERSION  # noqa: E402

# Uploaded files are held in PostgreSQL for now, so they are covered by the
# database's existing backups and point-in-time recovery with nothing extra to
# provision. See CLAUDE.md: this moves to S3 when object storage is set up.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# The logo is capped far lower than a general upload: it is drawn about 32px
# tall, so 512 KB is already generous for a retina-resolution PNG.
MAX_LOGO_BYTES = 512 * 1024

# A byte cap alone does not protect the browser. A 10000x10000 PNG of flat
# color compresses to a few kilobytes and still has to be decoded into ~400 MB
# of bitmap. Cap the pixels as well.
MAX_LOGO_DIMENSION = 2000

# Where the logo may sit in relation to the title in the sidebar.
LOGO_POSITIONS = ["left", "right", "above", "below"]

# How each record type is named where a person reads it. The database calls
# them system / data_flow / data_element / contributor; nobody else should have
# to.
ENTITY_LABELS = {
    "system": "System",
    "data_flow": "Data Flow",
    "data_element": "Data Element",
    "contributor": "Contributor",
    "task": "Task",
}

# A field definition is a tooltip, not an essay. Long enough for a sentence or
# two of real explanation, short enough that it stays readable in a bubble
# beside the field.
MAX_FIELD_DEFINITION = 600

# Colors a SuperAdmin may choose for a system type. Every combination has been
# checked against WCAG 2.1 AA — text on fill exceeds 4.5:1 and the border
# exceeds 3:1 against the canvas. See ACCESSIBILITY.md for the measurements.
TYPE_PALETTE = {
    "blue":    {"fill": "#dce9fb", "stroke": "#2f6fd0", "text": "#123a6b", "label": "Blue"},
    "green":   {"fill": "#c9e6d2", "stroke": "#116039", "text": "#0d3d22", "label": "Green"},
    "purple":  {"fill": "#e8daef", "stroke": "#7d3c98", "text": "#5b2c6f", "label": "Purple"},
    "orange":  {"fill": "#fce5cd", "stroke": "#b35c00", "text": "#7a3e00", "label": "Orange"},
    "teal":    {"fill": "#d3efee", "stroke": "#0f6f6a", "text": "#0a4a47", "label": "Teal"},
    "slate":   {"fill": "#e2e6ea", "stroke": "#4a5764", "text": "#333f4a", "label": "Slate"},
    "magenta": {"fill": "#fadcec", "stroke": "#a3216b", "text": "#6e1748", "label": "Magenta"},
    "olive":   {"fill": "#e6ecc9", "stroke": "#5f6d18", "text": "#414a10", "label": "Olive"},
}

# Files a browser can display inline. Everything else is offered as a download
# only — browsers cannot render Office formats without a conversion service.
INLINE_CONTENT_TYPES = {
    "application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp",
    "image/svg+xml", "text/plain", "text/csv",
}

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

# Fields the record cannot do without. The database marks these NOT NULL, but an
# empty string satisfies that constraint — so blanking a name in the UI would
# save successfully and leave an unnamed record behind. These are checked here.
REQUIRED_FIELDS = {
    "system": [("name", "Name"), ("system_type", "Type"), ("managed_by", "Managed By")],
    # Transfer Method and Format are deliberately not required: the Add Data
    # Flow modal never collected them, so demanding them on edit meant a record
    # you had just created could not be saved again without filling in fields
    # the app never asked for.
    "data_flow": [("name", "Name"), ("source_system_id", "Source System"),
                  ("destination_system_id", "Destination System"),
                  ("frequency", "Frequency")],
    "data_element": [("name", "Name"), ("definition", "Definition")],
    "contributor": [("name", "Name"), ("organization_unit", "Organization Unit")],
}


def missing_required(entity, incoming, existing=None):
    """Return the labels of required fields that would end up blank.

    ``incoming`` is the submitted payload and ``existing`` the current row, so a
    field simply left out of a partial update keeps its value.
    """
    existing = existing or {}
    gaps = []
    for key, label in REQUIRED_FIELDS.get(entity, []):
        value = incoming.get(key, existing.get(key))
        if value is None or str(value).strip() == "":
            gaps.append(label)
    return gaps


# Optional foreign keys. Blanking one of these in a form must store NULL, not
# an empty string, or PostgreSQL rejects it against the referenced table.
FK_FIELDS = {"owner_id", "data_steward_id", "data_owner_id", "agreement_id",
             "target_system_id", "source_of_truth", "contributor_id"}


def fk(value):
    """Normalize an optional foreign key coming from a form.

    A dropdown left on "none" submits an empty string, not null. PostgreSQL
    rejects that against a foreign key and the request dies as a 500 — which is
    how leaving Owner blank on Add Data Flow produced an unexplained error.
    Anything blank becomes NULL, which is what "not set" is supposed to mean.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Connection points: three per side, twelve in all. Named rather than stored as
# coordinates so an anchor follows its box when the box is moved or resized.
ANCHOR_SIDES = ("top", "right", "bottom", "left")
ANCHORS_PER_SIDE = 3
MAX_WAYPOINTS = 12
# About what fits on an arrow without covering the diagram behind it.
MAX_DISPLAY_NAME = 20


def valid_anchor(value):
    """Whether ``value`` is a connection point such as ``top:1``."""
    if not isinstance(value, str) or ":" not in value:
        return False
    side, _, index = value.partition(":")
    return side in ANCHOR_SIDES and index.isdigit() and 0 <= int(index) < ANCHORS_PER_SIDE


class ImageRejected(Exception):
    """An uploaded image failed inspection. The message is shown to the user."""


def inspect_image(body):
    """Identify an uploaded logo from its *contents*, and measure it.

    Returns ``(content_type, width, height)`` for a PNG or JPEG, and raises
    ``ImageRejected`` for anything else.

    **The file extension and the browser-supplied content type are both
    ignored**, deliberately. Either can be set to anything by whoever uploads
    the file; renaming ``payload.html`` to ``logo.png`` would defeat a check
    based on the name. What comes back from here is what gets stored and what
    the endpoint later serves, so the served type always matches the bytes.

    SVG is *not* accepted, and should not be added: an SVG is a document that
    can carry script, and this one would be served from our own origin.

    Reading the dimensions needs no imaging library — both formats state them
    in the header — which keeps the dependency list as it is.
    """
    if not body:
        raise ImageRejected("The file is empty.")

    if body[:8] == b"\x89PNG\r\n\x1a\n":
        # IHDR is required by the specification to be the first chunk, so the
        # dimensions are always at a fixed offset.
        if len(body) < 24 or body[12:16] != b"IHDR":
            raise ImageRejected("That PNG file appears to be damaged.")
        width, height = struct.unpack(">II", body[16:24])
        return "image/png", width, height

    if body[:3] == b"\xff\xd8\xff":
        width, height = _jpeg_dimensions(body)
        return "image/jpeg", width, height

    raise ImageRejected(
        "That file is not a PNG or JPEG image. Only .png and .jpg files can be "
        "used as a logo.")


def _jpeg_dimensions(body):
    """Width and height from a JPEG's frame header.

    A JPEG is a chain of segments; the dimensions live in the start-of-frame
    one, whose position varies because the metadata before it does. So walk the
    chain rather than guessing an offset.
    """
    i, n = 2, len(body)
    while i < n - 1:
        if body[i] != 0xFF:               # resynchronize on padding
            i += 1
            continue
        marker = body[i + 1]
        i += 2
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue                      # standalone markers carry no payload
        if marker == 0xD9 or marker == 0xDA:
            break                         # end of image, or start of the scan
        if i + 2 > n:
            break
        length = struct.unpack(">H", body[i:i + 2])[0]
        # SOF0-SOF15 hold the frame size. C4, C8 and CC are in the same numeric
        # range but are Huffman/arithmetic tables, not frames.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 7 > n:
                break
            height, width = struct.unpack(">HH", body[i + 3:i + 7])
            return width, height
        if length < 2:
            break                         # malformed: would not advance
        i += length
    raise ImageRejected("That JPEG file appears to be damaged.")


def validate_logo(body):
    """Full check for an uploaded logo: type, byte size, and pixel size.

    Order matters. The size check comes first because it is the cheapest, and
    the dimension check comes last because it needs the file parsed.
    """
    if len(body) > MAX_LOGO_BYTES:
        raise ImageRejected(
            f"That image is {len(body)//1024} KB. The limit is "
            f"{MAX_LOGO_BYTES//1024} KB — the logo is displayed about 32 "
            f"pixels tall, so it does not need to be large.")
    content_type, width, height = inspect_image(body)
    if width <= 0 or height <= 0:
        raise ImageRejected("That image reports no size and cannot be used.")
    if width > MAX_LOGO_DIMENSION or height > MAX_LOGO_DIMENSION:
        raise ImageRejected(
            f"That image is {width}x{height} pixels. The limit is "
            f"{MAX_LOGO_DIMENSION} pixels on either side.")
    return content_type, width, height


def branding_summary(row):
    """Describe the stored logo without carrying the image itself.

    The bytes are fetched separately from ``/api/branding/logo``, so this stays
    small enough to sit in the settings payload that every page load fetches.
    """
    if not row:
        return None
    return {"filename": row["filename"], "content_type": row["content_type"],
            "size_bytes": row["size_bytes"], "width": row["width"],
            "height": row["height"], "etag": row["etag"],
            "uploaded_at": row["uploaded_at"]}


def change_state_for_new(handler, data):
    """The change state a newly created record should carry.

    New things in a scenario are proposals by default — that is what someone
    doing scenario planning almost always means, and the checkbox can be
    cleared to say "assume this already exists". Outside a scenario there is
    nothing to propose *to*, so the live diagram only ever holds ``unchanged``.
    """
    if handler.scenario_id is None:
        return scenarios.UNCHANGED
    proposed = data.get("proposed", True)
    if isinstance(proposed, str):
        proposed = proposed.strip().lower() in ("1", "true", "yes", "on")
    return scenarios.PROPOSED if proposed else scenarios.UNCHANGED


def reject_proposed_on_live(handler, data):
    """Guard: 'proposed' is meaningless outside a scenario, so refuse it.

    Silently ignoring it would let the frontend drift into sending a flag that
    does nothing, which is how a checkbox ends up looking saved but isn't.
    """
    if handler.scenario_id is None and data.get("proposed"):
        return ("Only records inside a scenario can be marked as proposed. "
                "Current Architecture describes what exists today.")
    return None


def mark_modified(conn, table, pk_column, pk_value, scenario_id):
    """Flag a copied record as edited, so a scenario reads as a change set.

    Only rows copied from live become ``modified``. Something ``proposed`` in
    this scenario stays proposed however often it is edited — it is still new
    — and something ``removed`` stays removed.
    """
    if scenario_id is None:
        return
    conn.execute(
        f"""UPDATE {table} SET change_state=?
             WHERE {pk_column}=? AND scenario_id=? AND change_state=?""",
        (scenarios.MODIFIED, pk_value, scenario_id, scenarios.UNCHANGED))


def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Governance inheritance

# What a delete takes with it. The confirmation used to describe the cascade in
# general terms — "any schema, attached documents and links go with it" — which
# is true and tells you nothing about the particular record in front of you.
# Deleting a system with 1,240 schema columns and three attached documents is a
# different act from deleting one someone created by mistake five minutes ago,
# and the dialog should be able to tell them apart.
def delete_tasks_for(conn, kind, parent_id):
    """Remove a parent's tasks, with their steps and assignments.

    A task has no meaning without the thing it is attached to — a validation
    step for a flow that no longer exists is not work anybody can do — so it
    goes with its parent rather than being orphaned or blocking the delete.
    The confirmation dialog counts them first (`delete_impact`), so this is
    never a surprise.
    """
    ids = [r["task_id"] for r in conn.execute(
        "SELECT task_id FROM task WHERE anchor_kind=? AND anchor_id=?",
        (kind, parent_id)).fetchall()]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM task_step WHERE task_id IN ({marks})", tuple(ids))
    conn.execute(f"DELETE FROM task_contributor WHERE task_id IN ({marks})",
                 tuple(ids))
    # Both columns. A row naming a deleted task as a DEPENDENCY would leave
    # whatever waits on it blocked forever against something that no longer
    # exists, which the color rules would draw as a red circle nobody can
    # clear.
    conn.execute(
        f"DELETE FROM task_dependency WHERE task_id IN ({marks}) "
        f"OR depends_on_task_id IN ({marks})", tuple(ids) + tuple(ids))
    conn.execute(
        f"DELETE FROM resource_version WHERE resource_id IN "
        f"(SELECT resource_id FROM resource WHERE entity_type='task' "
        f" AND entity_id IN ({marks}))", tuple(ids))
    conn.execute(
        f"DELETE FROM resource WHERE entity_type='task' "
        f"AND entity_id IN ({marks})", tuple(ids))
    conn.execute(f"DELETE FROM task WHERE task_id IN ({marks})", tuple(ids))
    return len(ids)


def delete_impact(conn, kind, record_id):
    """Counts of everything a delete would destroy, plus anything blocking it."""
    out = {"counts": [], "blockers": [], "heavy": False}

    def add(label, n):
        if n:
            out["counts"].append({"label": label, "count": n})

    def one(sql, params):
        return conn.execute(sql, params).fetchone()[0]

    if kind == "system":
        tables = one("SELECT COUNT(*) FROM schema_table WHERE system_id=?",
                     (record_id,))
        columns = one(
            "SELECT COUNT(*) FROM schema_column WHERE table_id IN "
            "(SELECT table_id FROM schema_table WHERE system_id=?)",
            (record_id,))
        add("database table", tables)
        add("database column", columns)
        add("uploaded schema version",
            one("SELECT COUNT(*) FROM schema_snapshot WHERE system_id=?",
                (record_id,)))
        add("attached document or link",
            one("SELECT COUNT(*) FROM resource "
                "WHERE entity_type='system' AND entity_id=?", (record_id,)))
        add("task",
            one("SELECT COUNT(*) FROM task "
                "WHERE anchor_kind='system' AND anchor_id=?", (record_id,)))
        # Typing the name is asked for only when there is a schema to lose.
        # That is the delete that cannot be reconstructed by hand, and the one
        # where the count in the dialog is a number nobody expects.
        out["heavy"] = bool(tables or columns)

        # Not a count: a system used by a flow cannot be deleted at all, so
        # this is the reason the button will refuse rather than a consequence.
        flows = conn.execute(
            "SELECT flow_id, name FROM data_flow "
            "WHERE (source_system_id=? OR destination_system_id=?) "
            "AND scenario_id IS NULL ORDER BY flow_id",
            (record_id, record_id)).fetchall()
        if flows:
            out["blockers"].append({
                "reason": "Used by data flows. Delete or repoint these first.",
                "count": len(flows),
                "named": [{"id": r["flow_id"], "label": r["name"]}
                          for r in flows[:MAX_NAMED_REFERENCES]],
            })

    elif kind == "flow":
        add("data element link",
            one("SELECT COUNT(*) FROM flow_element WHERE flow_id=?",
                (record_id,)))
        add("attached document or link",
            one("SELECT COUNT(*) FROM resource "
                "WHERE entity_type='data_flow' AND entity_id=?", (record_id,)))
        add("task",
            one("SELECT COUNT(*) FROM task "
                "WHERE anchor_kind='data_flow' AND anchor_id=?", (record_id,)))

    return out


# Where a contributor is named. Used to warn before deactivating one: the
# assignments are kept, so the point of the warning is scale and specificity —
# "this person is the Data Steward on these four systems" — rather than asking
# permission to destroy something.
CONTRIBUTOR_REFERENCES = [
    ("system", "system", "system_id", "name", ["owner_id", "data_steward_id"]),
    ("data_flow", "data_flow", "flow_id", "name", ["owner_id", "data_steward_id"]),
    ("data_element", "data_element", "element_id", "name",
     ["data_owner_id", "data_steward_id"]),
    ("schema_table", "schema_table", "table_id", "table_name",
     ["data_owner_id", "data_steward_id"]),
    ("schema_column", "schema_column", "column_id", "column_name",
     ["data_owner_id", "data_steward_id"]),
]

MAX_NAMED_REFERENCES = 10


def contributor_references(conn, cid):
    """What names this contributor, for the warning before deactivating.

    Names up to `MAX_NAMED_REFERENCES` records per kind and counts the rest.
    A warning that lists four hundred schema columns is one nobody reads, and
    a warning that gives only a number does not tell you whether it matters.

    Scenario copies are reported separately. A contributor can exist as a copy
    inside a scenario, and deactivating the live record says nothing about
    those, so the scenarios holding one are named rather than silently ignored.
    """
    out = {"records": [], "total": 0, "linked_user": None, "scenarios": []}
    for kind, table, pk, label_col, columns in CONTRIBUTOR_REFERENCES:
        clause = " OR ".join(f"{c}=?" for c in columns)
        rows = conn.execute(
            f"SELECT {pk} AS id, {label_col} AS label FROM {table} "
            f"WHERE {clause} ORDER BY {pk}",
            tuple([cid] * len(columns))).fetchall()
        if not rows:
            continue
        out["records"].append({
            "kind": kind,
            "label": ENTITY_LABELS.get(kind, kind.replace("_", " ").title()),
            "count": len(rows),
            "named": [{"id": r["id"], "label": r["label"]}
                      for r in rows[:MAX_NAMED_REFERENCES]],
        })
        out["total"] += len(rows)

    user = conn.execute(
        "SELECT user_id, email, is_active FROM app_user WHERE contributor_id=?",
        (cid,)).fetchone()
    if user:
        # Named, not counted: there is at most one, and which account it is
        # matters more than that there is one.
        out["linked_user"] = {"user_id": user["user_id"], "email": user["email"],
                              "is_active": user["is_active"]}

    copies = conn.execute(
        """SELECT s.scenario_id, s.name FROM contributor c
             JOIN scenario s ON s.scenario_id = c.scenario_id
            WHERE c.origin_id = ? AND c.scenario_id IS NOT NULL
            ORDER BY s.name""", (cid,)).fetchall()
    out["scenarios"] = [{"scenario_id": r["scenario_id"], "name": r["name"]}
                        for r in copies]
    return out


def linked_accounts(conn):
    """{contributor_id: {email, title}} for contributors with an account.

    Only active accounts. A deactivated account's address is no longer one
    anybody should be emailing, and its title is no longer current.
    """
    return {r["contributor_id"]: {"email": r["email"], "title": r["title"]}
            for r in conn.execute(
                "SELECT contributor_id, email, title FROM app_user "
                "WHERE contributor_id IS NOT NULL AND is_active=1").fetchall()}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
TASK_STATUSES = ["not_started", "in_progress", "completed"]
TASK_STATUS_LABELS = {
    "not_started": "Not started",
    "in_progress": "In progress",
    "completed": "Completed",
}
# "Other" is always available, and is deliberately NOT a row in `task_type`.
# See migration 0018: a seeded row would appear for new installations and never
# for an existing one, and a typed value landing in `task_type` would make
# every Editor a type author, which is the boundary TaskTypesHandler draws.
TASK_TYPE_OTHER = "Other"


def task_type_label(row):
    """What to show for a task's type: "Other \u2014 whatever they typed"."""
    base = row.get("task_type") or ""
    if base == TASK_TYPE_OTHER and (row.get("task_type_other") or "").strip():
        return f"{base} \u2014 {row['task_type_other'].strip()}"
    return base


TASK_ANCHORS = {
    # anchor_kind -> (table, primary key, display column)
    "system": ("system", "system_id", "name"),
    "data_flow": ("data_flow", "flow_id", "name"),
}


def derived_task_status(step_rows):
    """The status a task's subtasks add up to, or None when there is none.

    A task with subtasks does not carry its own status: it would let one read
    Completed with three subtasks unfinished underneath, and nothing would say
    so. The rule is the obvious one, with the ordering that matters — *any*
    subtask started means the task is under way, even if most are untouched,
    because "not started" would be false the moment somebody began.

    **A subtask with no status is ignored** (migration 0019). Nobody having
    said is different from somebody having said "not started", so a task whose
    subtasks are all unset has no status either rather than claiming one on
    their behalf — which is the lie the NOT NULL default used to tell.

    Returns None both when there are no subtasks and when none of them says
    anything. Callers that need to tell those apart check `step_rows`
    themselves: the difference matters because **once a task has subtasks its
    status is theirs**, so "they say nothing" has to mean the task has no
    status, not that a value somebody typed earlier shows through. The flyout
    labels the field "Determined based on subtasks" — leaving a stale hand-set
    value visible under that caption would be a plain contradiction.
    """
    if not step_rows:
        return None
    states = [r["status"] for r in step_rows]
    said = [st for st in states if st]
    if not said:
        return None
    # "All completed" is over **every** subtask, not only the ones with a
    # status. Testing it against the ones that said something made one finished
    # subtask out of three report the whole task Completed, because the other
    # two had been filtered out of the question — an unset subtask is not a
    # finished one.
    if all(st == "completed" for st in states):
        return "completed"
    if any(st in ("in_progress", "completed") for st in said):
        return "in_progress"
    return "not_started"


def task_rows(conn, where, params, include_children=True):
    """Tasks for a view, each carrying its steps, its people and its parent.

    One assembled shape for the catalog, the flyout and the diagram, so the
    three cannot disagree about what a task is. The alternative — each caller
    joining what it happens to need — is how a status comes to read one way in
    a table and another in a flyout.
    """
    rows = rows_to_list(conn.execute(
        f"SELECT * FROM task WHERE {where} ORDER BY task_id", params).fetchall())
    if not rows:
        return []
    ids = [r["task_id"] for r in rows]
    marks = ",".join("?" for _ in ids)

    steps, people, deps = {}, {}, {}
    if include_children:
        for st in conn.execute(
                f"SELECT * FROM task_step WHERE task_id IN ({marks}) "
                f"ORDER BY sort_order, step_id", tuple(ids)).fetchall():
            steps.setdefault(st["task_id"], []).append(dict(st))
        for tc in conn.execute(
                f"SELECT task_id, contributor_id FROM task_contributor "
                f"WHERE task_id IN ({marks}) ORDER BY contributor_id",
                tuple(ids)).fetchall():
            people.setdefault(tc["task_id"], []).append(tc["contributor_id"])
        for td in conn.execute(
                f"SELECT task_id, depends_on_task_id FROM task_dependency "
                f"WHERE task_id IN ({marks}) ORDER BY depends_on_task_id",
                tuple(ids)).fetchall():
            deps.setdefault(td["task_id"], []).append(td["depends_on_task_id"])

    # A dependency is carried with its NAME and its RESOLVED status, not as a
    # bare identifier. Two reasons, and the second is the one that matters:
    #
    # * the flyout has to say "Validate enrollment file", and it is reading one
    #   task, so it has no catalog to look the name up in;
    # * the color rules need the status a dependency would *report*, which for
    #   a task with subtasks is derived rather than stored. Resolving it here
    #   means `taskSignal` in the browser compares the same value the catalog
    #   shows, and cannot disagree with it.
    #
    # Fetched directly rather than by calling task_rows again: a dependency may
    # point at a task outside this result set, and a recursive call would walk
    # the whole chain to answer a question that only needs one hop.
    dep_info = {}
    wanted = sorted({d for row_deps in deps.values() for d in row_deps})
    if wanted:
        dmarks = ",".join("?" for _ in wanted)
        dep_steps = {}
        for st in conn.execute(
                f"SELECT task_id, status FROM task_step "
                f"WHERE task_id IN ({dmarks})", tuple(wanted)).fetchall():
            dep_steps.setdefault(st["task_id"], []).append(dict(st))
        for dr in conn.execute(
                f"SELECT task_id, name, status FROM task "
                f"WHERE task_id IN ({dmarks})", tuple(wanted)).fetchall():
            own = dep_steps.get(dr["task_id"], [])
            status = derived_task_status(own) if own else dr["status"]
            dep_info[dr["task_id"]] = {
                "task_id": dr["task_id"], "name": dr["name"], "status": status}

    # Parent names, so a catalog row can say what the task is attached to
    # without the browser holding every system and flow.
    parents = {}
    for kind, (table, pk, label) in TASK_ANCHORS.items():
        wanted = [r["anchor_id"] for r in rows if r["anchor_kind"] == kind]
        if not wanted:
            continue
        m = ",".join("?" for _ in wanted)
        for pr in conn.execute(
                f"SELECT {pk} AS id, {label} AS label FROM {table} "
                f"WHERE {pk} IN ({m})", tuple(wanted)).fetchall():
            parents[(kind, pr["id"])] = pr["label"]

    for r in rows:
        r["steps"] = steps.get(r["task_id"], [])
        r["contributor_ids"] = people.get(r["task_id"], [])
        # Both shapes: the ids are what the edit form binds to and sends back,
        # the resolved rows are what the read view and the color rules use.
        # A dependency whose task has since been deleted simply drops out —
        # the cascade should have removed the row, and showing a dangling
        # identifier would be worse than showing nothing.
        r["dependency_ids"] = [d for d in deps.get(r["task_id"], [])
                               if d in dep_info]
        r["dependencies"] = [dep_info[d] for d in r["dependency_ids"]]
        r["anchor_name"] = parents.get((r["anchor_kind"], r["anchor_id"]))
        # The stored status is authoritative only for a task with no subtasks.
        # Resolved on read as well as on write, so a row edited directly in the
        # database still reports something true. Once there ARE subtasks the
        # answer is theirs, including when it is "nothing" — see
        # derived_task_status.
        has_steps = bool(r["steps"])
        derived = derived_task_status(r["steps"])
        r["status"] = derived if has_steps else r["status"]
        # An unset status has no label. Each surface decides how to show the
        # absence — the catalog a dash, the circle's tooltip nothing at all.
        r["status_label"] = TASK_STATUS_LABELS.get(r["status"]) if r["status"] else None
        r["type_label"] = task_type_label(r)
        r["status_from_steps"] = has_steps

        # The task's own dates win; where it has none, the subtasks supply the
        # window. Ben's point was that entering dates on subtasks and then
        # finding the task itself blank is confusing — the task's pair IS the
        # outer window, so when nobody has said otherwise the subtasks are the
        # best statement of it.
        #
        # Carried as `effective_*` alongside the raw columns rather than
        # replacing them, the same way `effective_definition` and
        # `effective_title` already work here. That distinction is what makes
        # the override possible: the edit form shows and sends the **raw**
        # value, so saving a task without touching its dates leaves them unset
        # and still derived, while typing one stores it and it wins from then
        # on. Overwriting the raw key would make every save a silent override.
        d_start, d_end = derived_task_dates(r["steps"])
        r["derived_start"], r["derived_end"] = d_start, d_end
        r["effective_expected_start"] = r["expected_start"] or d_start
        r["effective_expected_end"] = r["expected_end"] or d_end
        # Per end, because a task may name its own start and leave the finish
        # to its subtasks.
        r["start_from_steps"] = not r["expected_start"] and bool(d_start)
        r["end_from_steps"] = not r["expected_end"] and bool(d_end)
    return rows


def derived_task_dates(step_rows):
    """The window a task's subtasks occupy: (earliest start, latest end).

    Either half is None when no subtask carries that end. Only the subtasks
    that HAVE a date take part — a subtask with no start date says nothing
    about when the work begins, and treating a missing date as "now" or as
    "forever" would both be inventions.

    **Nothing is stored.** This is resolved on read, exactly as a derived
    status is, and for the same reason: a value copied into the task's own
    columns is correct at the moment of the copy and silently wrong the moment
    a subtask moves. A stored span would also have to be kept in step by every
    path that touches a subtask — the update handler, the copy, a scenario
    clone — and the one that got missed would leave a task claiming a window
    that does not contain its own subtasks. Deriving it means it cannot be
    stale and no migration was needed.
    """
    starts = [r.get("expected_start") for r in (step_rows or [])
              if r.get("expected_start")]
    ends = [r.get("expected_end") for r in (step_rows or [])
            if r.get("expected_end")]
    # Dates are 'YYYY-MM-DD', so min/max on the strings is min/max on the
    # dates, with nothing to parse.
    return (min(starts) if starts else None, max(ends) if ends else None)


def apply_task_status(conn, task_id):
    """Recompute and store a task's status after its subtasks change.

    A task with **no** subtasks keeps the status somebody set by hand. A task
    **with** subtasks has theirs written to it, even when that is NULL: the
    field is presented as derived, so a hand-set value left showing underneath
    would contradict its own caption.
    """
    steps = conn.execute(
        "SELECT status FROM task_step WHERE task_id=?", (task_id,)).fetchall()
    if not steps:
        return None
    derived = derived_task_status(steps)
    conn.execute("UPDATE task SET status=? WHERE task_id=?", (derived, task_id))
    return derived


def contact_emails(conn):
    """{contributor_id: email} preferring the linked user account.

    A person can exist twice: as a `contributor` (the governance record) and as
    an `app_user` (the sign-in account), joined by `app_user.contributor_id`.
    Both carry an email and they can disagree.

    The account's address wins, because it is the one that provably works —
    they sign in with it — while a contributor's own email is typed in by hand
    and goes stale when someone changes name or department. The contributor's
    own address is the fallback for the many contributors who have no account.

    Resolved on the server so the rule lives in one place; the browser is handed
    an answer rather than two addresses and a precedence rule to re-implement.
    """
    out = {}
    for r in conn.execute(
            "SELECT contributor_id, email FROM contributor "
            "WHERE email IS NOT NULL AND email != ''").fetchall():
        out[r["contributor_id"]] = r["email"]
    for cid, acct in linked_accounts(conn).items():
        out[cid] = acct["email"]
    return out


def with_contact(rows, conn):
    """Add the resolved contact details to contributor rows.

    `account_title` is set only when a linked account exists, and the frontend
    uses its presence to decide whether the contributor's own Title field is
    editable: a person with an account maintains their title themselves, in
    their profile, so editing the contributor copy would be writing to a value
    nothing reads.
    """
    emails = contact_emails(conn)
    accounts = linked_accounts(conn)
    out = rows_to_list(rows) if not isinstance(rows, list) else rows
    for r in out:
        cid = r.get("contributor_id")
        acct = accounts.get(cid) or {}
        r["contact_email"] = emails.get(cid)
        r["account_title"] = acct.get("title")
        # The account's title wins where there is one; a contributor who will
        # never sign in keeps their own.
        r["effective_title"] = acct.get("title") or r.get("title")
    return out

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
    """Put a small fictional district in an empty database, so a new
    installation has something to look at.

    **Everything here is invented.** Until 0.9.0 this was Maine DOE's actual
    architecture — eight real systems, the real SIF/CONNECT pipeline, and 33
    data elements from the state's SIF profile. That was right while this was a
    one-department tool and wrong the moment it was shared: an adopter starting
    from an empty database would watch another state's systems appear on first
    run and have to work out which parts were examples and which were theirs.

    So the names are **roles rather than products** — "Statewide Integration
    Platform" rather than any particular vendor or state's name for one. The
    shape is deliberately the same as a real deployment, because the point of a
    seed is to demonstrate the features: layered systems from source to
    reporting, flows that carry PII and flows that do not, elements with
    different classifications, and one mapped database schema to drill into.
    An adopter deletes all of it once they have seen what it does.

    ("Two schemas" until this was corrected — that counted the two real seeds
    0.9.0 removed. There is one now, `sample_schema.json`, four tables across
    a `core` and a `reporting` namespace on SYS-003.)

    Seeded only into a database with no systems, so it never reappears after
    somebody clears it out.
    """
    conn = get_db()
    # Only seed if no systems exist yet
    if conn.execute("SELECT COUNT(*) FROM system").fetchone()[0] > 0:
        conn.close()
        return

    now = datetime.datetime.now().isoformat()

    # Contributors. Role titles rather than invented people: a name in a seed
    # is a name somebody has to recognise as fictional, and these records
    # exist to show where governance accountability attaches, not to populate
    # a staff directory. Email and phone stay NULL for the same reason.
    conn.execute("""INSERT INTO contributor (contributor_id,name,title,organization_unit,email,phone,
        role_type,notes,governance_role,governance_responsibilities,training_status,last_training_date,
        created_date,updated_date) VALUES
        ('CON-001','Data Integration Manager','Manager, Data Integration','Data Management Division',
         NULL,NULL,'Data Owner','Primary contact for the collection pipeline',
         'Data Owner','Oversees data collection policies, approves data sharing agreements, ensures compliance with FERPA and state regulations',
         'Current','2026-09-15',?,?)""", (now, now))
    conn.execute("""INSERT INTO contributor (contributor_id,name,title,organization_unit,email,phone,
        role_type,notes,governance_role,governance_responsibilities,training_status,last_training_date,
        created_date,updated_date) VALUES
        ('CON-002','Data Store Administrator','Database Administrator','IT Infrastructure',
         NULL,NULL,'System Admin','Manages the operational data store infrastructure',
         'Data Steward','Day-to-day maintenance of the operational and longitudinal stores, data quality monitoring, backup and recovery operations',
         'Current','2026-10-01',?,?)""", (now, now))

    # Systems. Eight, laid out left to right from collection through to
    # reporting, so the diagram opens on something legible.
    systems = [
        ("SYS-001", "District Student Information Systems",
         "Student information systems operated by local school districts. The primary source of student demographic, enrollment and discipline data.",
         "Source", "Various", "SIS Platforms", "Production",
         "Local School Districts", "CON-001", "School Districts", 1, 1, "Active",
         "Multiple vendors across districts; data quality varies by district", 100, 250,
         "Confidential", "Varies by district", "Varies by vendor", "HTTPS/TLS 1.2", None, 0,
         None, "Per district policy", "Per district policy", "Varies", "Varies",
         "Interoperability standard with state extensions", "District staff with SIS access",
         "FERPA restrictions apply"),
        ("SYS-002", "Statewide Integration Platform",
         "Integration middleware that receives data from district systems and routes it to the operational data store. Performs initial validation.",
         "Data Store", "State-developed", "Integration middleware", "Production",
         "Data Management Division", "CON-001", "Department of Education", 1, 1, "Active",
         "Carries the state interoperability profile and its extensions", 400, 250,
         "Confidential", "Role-based access", "AES-256", "TLS 1.2", "2026-06-15", 1,
         "CON-001", "7 years per state policy", "Secure deletion", "Daily", "Documented",
         "Interoperability standard", "Data Management Division staff",
         "No external sharing without agreement"),
        ("SYS-003", "Operational Data Store",
         "Central repository receiving validated data from the integration platform. Holds current-year operational data for all students, schools and districts.",
         "Data Store", "State-developed", "Relational database", "Production",
         "IT Infrastructure", "CON-002", "Department of Education", 1, 1, "Active",
         "Source of truth for current-year student data", 700, 250,
         "Confidential", "Role-based access with directory integration",
         "Transparent data encryption", "TLS 1.2", "2026-08-01", 1,
         "CON-002", "7 years per state policy", "Secure deletion", "Nightly", "Documented",
         "Interoperability standard", "Department analysts and administrators",
         "Aggregate reporting only without an agreement"),
        ("SYS-004", "Longitudinal Data System",
         "Multi-year store built from annual snapshots of the operational data store. Supports cohort and trend analysis.",
         "Data Store", "State-developed", "Relational database", "Production",
         "IT Infrastructure", "CON-002", "Department of Education", 1, 1, "Active",
         "Retains history the operational store does not", 700, 420,
         "Confidential", "Role-based access", "Transparent data encryption", "TLS 1.2",
         "2026-08-01", 1, "CON-002", "Indefinite for research", "Secure deletion",
         "Nightly", "Documented", "Interoperability standard", "Research and policy staff",
         "De-identified extracts only for external researchers"),
        ("SYS-005", "Federal Reporting Extracts",
         "Builds and submits the department's federal reporting files from operational data.",
         "Destination", "State-developed", "Extract and submission tooling", "Production",
         "Reporting Office", "CON-001", "Department of Education", 0, 0, "Active",
         "Aggregate counts only; no student-level records leave here", 1000, 160,
         "Public", "Role-based access", "AES-256", "TLS 1.2", "2026-07-01", 1,
         "CON-001", "5 years", "Secure deletion", "Per submission", "Documented",
         "Federal reporting specifications", "Reporting Office staff",
         "Published after review"),
        ("SYS-006", "Funding Calculations",
         "Calculates district funding allocations from enrollment and program participation counts.",
         "Destination", "State-developed", "Calculation engine", "Production",
         "School Finance", "CON-001", "Department of Education", 0, 0, "Active",
         "Allocations are published; the inputs are not", 1000, 300,
         "Internal", "Role-based access", "AES-256", "TLS 1.2", "2026-07-01", 1,
         "CON-001", "7 years", "Secure deletion", "Per cycle", "Documented",
         "State funding formula", "School Finance staff", "Published after certification"),
        ("SYS-007", "Public Reporting Portal",
         "Public-facing dashboards and report cards built from suppressed, aggregated longitudinal data.",
         "Destination", "Vendor-hosted", "Web application", "Production",
         "Communications", "CON-001", "Department of Education", 0, 0, "Active",
         "Small-cell suppression applied before anything reaches here", 1000, 440,
         "Public", "Public", "Vendor-managed", "TLS 1.3", "2026-09-01", 1,
         "CON-001", "Current and prior year", "Vendor deletion policy", "Vendor-managed",
         "Vendor-managed", "Public reporting standard", "Public",
         "Aggregates only; suppression rules applied"),
        ("SYS-008", "District Human Resources Systems",
         "District systems holding educator assignment and credential data, collected alongside student data.",
         "Source", "Various", "HR Platforms", "Production",
         "Local School Districts", "CON-001", "School Districts", 1, 0, "Active",
         "Educator data rather than student data; separate collection calendar", 100, 420,
         "Confidential", "Varies by district", "Varies by vendor", "HTTPS/TLS 1.2", None, 0,
         None, "Per district policy", "Per district policy", "Varies", "Varies",
         "Educator data standard", "District HR staff", "Personnel records restrictions apply"),
    ]
    for s in systems:
        conn.execute("""INSERT INTO system (system_id,name,description,system_type,vendor,technology,
            environment,managed_by,owner_id,organization,contains_pii,ferpa_protected,status,notes,
            x_pos,y_pos,data_classification,access_method,encryption_at_rest,encryption_in_transit,
            last_security_review,has_audit_trail,data_steward_id,retention_schedule,destruction_method,
            backup_frequency,disaster_recovery,data_standards,authorized_users,data_sharing_restrictions,
            created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            s + (now, now))

    # Data flows. Seven, including one that carries no PII (PL-006) and one on
    # a separate collection calendar (PL-007), so the diagram shows more than a
    # single straight pipeline.
    flows = [
        ("PL-001", "District Systems to Integration Platform",
         "Primary collection pipeline. Districts transmit student demographic, enrollment and discipline data to the state.",
         "SYS-001", "SYS-002", "Inbound", "Nightly", "API", "XML",
         "Validated against the state profile; rejected records are returned to the district",
         "Districts submit for their own students only",
         "Schema validation, required-field checks, duplicate identifier detection",
         "Rejected batches are reported back to the submitting district within the hour",
         1, 1, "State reporting requirement", None, "CON-001", "Active",
         "Late submissions from smaller districts are common at term boundaries",
         "Confidential", "TLS 1.2", "None applied at this stage",
         "Record counts reconciled against district totals",
         "Interoperability standard", "7 years per state policy",
         "State statute and FERPA"),
        ("PL-002", "Integration Platform to Operational Data Store",
         "Validated records are written into the operational store, where identifiers are resolved to the statewide student identifier.",
         "SYS-002", "SYS-003", "Outbound", "Nightly", "Database", "Relational",
         "Identifier resolution; district codes mapped to state codes; enrollment history assembled",
         "A student may be enrolled in only one district at a time",
         "Referential integrity, enrollment overlap detection",
         "Conflicts are queued for manual resolution by the data steward",
         1, 1, "State reporting requirement", None, "CON-002", "Active",
         "Enrollment overlaps need manual resolution a few times each term",
         "Confidential", "TLS 1.2", "None applied at this stage",
         "Row counts and identifier match rates monitored nightly",
         "Interoperability standard", "7 years per state policy",
         "State statute and FERPA"),
        ("PL-003", "Operational Store to Longitudinal Data System",
         "Annual snapshot copied into the longitudinal store at the close of the school year.",
         "SYS-003", "SYS-004", "Outbound", "Annually", "Database", "Relational",
         "Snapshot tagged with the school year; prior years are never modified",
         "Snapshots are taken after the certification deadline",
         "Row count comparison against the certified operational totals",
         "A failed snapshot is retried; the prior year remains the latest until it succeeds",
         1, 1, "Longitudinal reporting", None, "CON-002", "Active", None,
         "Confidential", "TLS 1.2", "None applied at this stage",
         "Snapshot totals reconciled against certified counts",
         "Interoperability standard", "Indefinite for research",
         "State statute and FERPA"),
        ("PL-004", "Operational Store to Federal Reporting Extracts",
         "Aggregate counts built for federal submission. No student-level records cross this boundary.",
         "SYS-003", "SYS-005", "Outbound", "Per submission", "File transfer", "CSV",
         "Aggregated to the school and district level; small cells suppressed",
         "Counts must match the certified operational totals",
         "Totals reconciled against the certified counts before submission",
         "A mismatch stops the submission and is raised with the Reporting Office",
         0, 0, "Federal reporting requirement", None, "CON-001", "Active",
         "Category definitions change between collection years",
         "Public", "TLS 1.2", "Small-cell suppression applied",
         "Totals reconciled against certified counts",
         "Federal reporting specifications", "5 years",
         "Federal reporting authority"),
        ("PL-005", "Operational Store to Funding Calculations",
         "Enrollment and program participation counts feeding the district funding formula.",
         "SYS-003", "SYS-006", "Outbound", "Per cycle", "Database", "Relational",
         "Counts taken as at the official enrollment date",
         "Only enrollments active on the count date are included",
         "Count-date totals compared against the prior cycle for unexplained movement",
         "An unexplained change is reviewed with School Finance before the cycle closes",
         0, 0, "State funding formula", None, "CON-001", "Active", None,
         "Internal", "TLS 1.2", "Aggregated to district level",
         "Compared against prior cycle",
         "State funding formula", "7 years", "State statute"),
        ("PL-006", "Longitudinal Store to Public Reporting Portal",
         "Suppressed aggregates published as dashboards and report cards.",
         "SYS-004", "SYS-007", "Outbound", "Annually", "API", "JSON",
         "Aggregated, small cells suppressed, complementary suppression applied",
         "No cell below the suppression threshold is published",
         "Suppression rules verified before publication",
         "A suppression failure blocks publication",
         0, 0, "Public reporting requirement", None, "CON-001", "Active", None,
         "Public", "TLS 1.3", "Small-cell and complementary suppression",
         "Suppression verified before each publication",
         "Public reporting standard", "Current and prior year",
         "State statute"),
        ("PL-007", "District HR Systems to Integration Platform",
         "Educator assignment and credential data, collected on its own calendar.",
         "SYS-008", "SYS-002", "Inbound", "Termly", "File transfer", "CSV",
         "Credential numbers validated against the certification registry",
         "An educator is reported by their primary assignment district",
         "Credential validation, assignment overlap detection",
         "Unmatched credentials are returned to the district for correction",
         1, 0, "State reporting requirement", None, "CON-001", "Active",
         "Credential numbers are sometimes entered inconsistently across districts",
         "Confidential", "TLS 1.2", "None applied at this stage",
         "Match rate against the certification registry monitored",
         "Educator data standard", "7 years per state policy",
         "State statute"),
    ]
    for f in flows:
        conn.execute("""INSERT INTO data_flow (flow_id,name,description,source_system_id,
            destination_system_id,direction,frequency,transfer_method,format,transformation_steps,
            business_rules,validations,error_handling,contains_pii,ferpa_protected,regulatory_driver,
            agreement_id,owner_id,status,known_issues,data_classification,encryption_method,
            disclosure_avoidance,data_quality_measures,data_standard,retention_at_destination,
            legal_authority,created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            f + (now, now))

    # Data elements. Twelve rather than the thirty-three this used to carry:
    # enough to cover every case the catalog has to display — PII and not,
    # FERPA and not, three classifications, a derived element and a coded one —
    # and few enough that somebody can read the whole list while deciding what
    # their own elements should look like.
    elements = [
        ("DE-001", "State Student Identifier", "SASID, SSID, State ID",
         "Unique student identifier assigned by the state. Stable across districts and school years.",
         "varchar(10)", "10 digits", 1, 1, "SYS-003",
         "The join key for every student-level flow", "Confidential",
         "Interoperability standard",
         "Lets a student be followed between districts without using their name",
         "CON-001", "CON-002"),
        ("DE-002", "Local Student Identifier", "District ID, Local ID",
         "Identifier assigned by the enrolling district. Unique within a district, not across the state.",
         "varchar(20)", None, 1, 1, "SYS-001",
         "Districts use this in their own reporting", "Confidential",
         "Interoperability standard",
         "How a district recognises the record as theirs", "CON-001", "CON-002"),
        ("DE-003", "Legal Last Name", "Surname, Family Name",
         "The student's legal last name as recorded by the enrolling district.",
         "varchar(60)", None, 1, 1, "SYS-001", None, "Confidential",
         "Interoperability standard",
         "Used for identity resolution when an identifier is missing",
         "CON-001", "CON-002"),
        ("DE-004", "Legal First Name", "Given Name",
         "The student's legal first name as recorded by the enrolling district.",
         "varchar(60)", None, 1, 1, "SYS-001", None, "Confidential",
         "Interoperability standard",
         "Used for identity resolution when an identifier is missing",
         "CON-001", "CON-002"),
        ("DE-005", "Date of Birth", "DOB, Birth Date",
         "The student's date of birth.",
         "date", None, 1, 1, "SYS-001",
         "Also used to derive age as at the enrollment count date", "Confidential",
         "Interoperability standard",
         "Determines age-based eligibility and grade placement", "CON-001", "CON-002"),
        ("DE-006", "Grade Level", "Grade, Year Level",
         "The grade in which the student is enrolled.",
         "varchar(2)", "PK, KG, 01-12", 0, 1, "SYS-001", None, "Confidential",
         "Interoperability standard",
         "Drives most reporting breakdowns and the funding formula",
         "CON-001", "CON-002"),
        ("DE-007", "Enrollment Entry Date", "Entry Date, Start Date",
         "The date the student's current enrollment began.",
         "date", None, 0, 1, "SYS-001",
         "An enrollment with no exit date is treated as active", "Confidential",
         "Interoperability standard",
         "Decides whether a student counts on the official count date",
         "CON-001", "CON-002"),
        ("DE-008", "Enrollment Exit Date", "Exit Date, Withdrawal Date",
         "The date the student's enrollment ended. Empty while the enrollment is active.",
         "date", None, 0, 1, "SYS-001",
         "Overlapping enrollments are the most common reason a record is queued for manual resolution",
         "Confidential", "Interoperability standard",
         "Distinguishes a transfer from a dropout when read with the exit reason",
         "CON-001", "CON-002"),
        ("DE-009", "Exit Reason Code", "Withdrawal Code",
         "Coded reason the enrollment ended.",
         "varchar(4)", "Transfer, Graduate, Dropout, Deceased, Other", 0, 1, "SYS-001",
         None, "Confidential", "Interoperability standard",
         "The basis of the graduation and dropout rates", "CON-001", "CON-002"),
        ("DE-010", "Program Participation Flags", "Program Indicators",
         "Whether the student participates in programs that affect funding or reporting, such as special education, English learner services or free and reduced-price meals.",
         "varchar(120)", "Comma-separated program codes", 1, 1, "SYS-003",
         "Assembled in the operational store rather than submitted as one field",
         "Confidential", "Interoperability standard",
         "Drives weighted funding and most federal subgroup reporting",
         "CON-001", "CON-002"),
        ("DE-011", "Attendance Rate", "ADA, Attendance Percentage",
         "Proportion of enrolled days the student was present, calculated over the reporting period.",
         "decimal(5,2)", "0.00-100.00", 0, 1, "SYS-003",
         "Derived rather than submitted; districts send daily attendance",
         "Confidential", "Interoperability standard",
         "Chronic absenteeism reporting is built on this", "CON-001", "CON-002"),
        ("DE-012", "Educator Credential Number", "Certificate Number, License Number",
         "Identifier of the educator's state teaching credential.",
         "varchar(20)", None, 1, 0, "SYS-008",
         "Educator data rather than student data; not FERPA-protected",
         "Confidential", "Educator data standard",
         "Links an assignment to a credential, which is what makes out-of-field teaching visible",
         "CON-001", "CON-002"),
    ]
    for e in elements:
        conn.execute("""INSERT INTO data_element (element_id,name,aliases,definition,data_type,
            valid_values,is_pii,ferpa_protected,source_of_truth,notes,data_classification,
            data_standard,business_meaning,data_owner_id,data_steward_id,created_date,updated_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", e + (now, now))

    # Which elements travel on which flow. The student elements ride the two
    # collection flows; the educator credential rides the HR flow only, which
    # is what makes a field list worth looking at rather than a formality.
    student_elements = [f"DE-{n:03d}" for n in range(1, 12)]
    for eid in student_elements:
        conn.execute("""INSERT INTO flow_element (flow_id, element_id, is_transformed,
            transformation_note) VALUES ('PL-001', ?, 0, NULL)""", (eid,))
    for eid in student_elements:
        note = ("Local identifier resolved to the state identifier"
                if eid == "DE-002" else None)
        conn.execute("""INSERT INTO flow_element (flow_id, element_id, is_transformed,
            transformation_note) VALUES ('PL-002', ?, ?, ?)""",
            (eid, 1 if note else 0, note))
    conn.execute("""INSERT INTO flow_element (flow_id, element_id, is_transformed,
        transformation_note) VALUES ('PL-007', 'DE-012', 0, NULL)""")

    conn.commit()
    conn.close()
    print(f"  Seeded a sample district: {len(systems)} systems, {len(flows)} flows, "
          f"{len(elements)} data elements")


# ---------------------------------------------------------------------------
# Schema drill-down seeding (loads *_schema.json into schema_table/column)
# ---------------------------------------------------------------------------

def seed_schema():
    """Populate schema_table / schema_column from any *_schema.json seed files.

    Each seed file declares the system_id it belongs to (e.g.
    sample_schema.json -> SYS-003). Runs independently of
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
        # Parent system must exist in this database, AND be the one this file
        # was written for.
        #
        # **The name check is not belt-and-braces; it closes a real hole.**
        # System ids are sequential (SYS-001, SYS-002, ...), so a seed file
        # naming SYS-003 collides with whatever happens to be third in any
        # other deployment. `sample_schema.json` names SYS-003, and in the
        # Maine database SYS-003 is the real operational data store.
        #
        # Matching on the id alone, the only thing standing between a
        # fictional schema and a real system was the "already has tables"
        # check below — and clearing a system's schema to re-upload a fresh
        # .sql is an ordinary maintenance action. The next restart would have
        # injected four invented tables into a real system, where they would
        # look mapped and could have governance attached to them.
        #
        # So the file has to agree about the name too. `sample_schema.json`
        # says "Operational Data Store"; Maine's SYS-003 is "NEO (State
        # Operational Data Store)". Different, so it can never be seeded
        # there. A fresh installation's SYS-003 is exactly that name, so the
        # sample still loads. A file with no `system_name` is refused rather
        # than trusted, because an unnamed file is one that predates this
        # check and cannot have been verified against anything.
        row = conn.execute("SELECT name FROM system WHERE system_id=?",
                           (system_id,)).fetchone()
        if not row:
            continue
        expected = (data.get("system_name") or "").strip()
        if not expected or row["name"].strip() != expected:
            print(f"  Skipping schema seed {path.name}: {system_id} is "
                  f"'{row['name']}' here, not '{expected or '(unnamed)'}'")
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
    # The logo itself is in app_branding, not here; see migration 0009. These
    # two are how it is arranged, which is small enough to be a preference.
    ("logo_position", "left", "logo_position", "Logo position",
     "Where the logo sits in relation to the title, in the sidebar.",
     "General", 1, 22),
    ("hide_app_title", "false", "boolean", "Hide the title text when a logo is set",
     "Use this when the logo already contains the name, so it is not shown "
     "twice. The title is still used for the browser tab and for screen "
     "readers.", "General", 1, 24),

    ("allowed_email_domains", "", "string", "Allowed email domains",
     "Comma-separated list of domains permitted for new accounts "
     "(e.g. example.gov, k12.example.us). Leave blank to allow any domain.",
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

    ("audit_retention_days", "0", "number", "Audit log retention (days)",
     "How long to keep audit history. 0 means keep everything forever, which is "
     "the default. Changing this does not delete anything on its own.",
     "Security", 1, 105),

    # Email (0.9.0). The settings live here so that setting this up does not
    # require access to the infrastructure the app runs on — in a state agency
    # the person who knows the mail relay is usually not the person who can
    # redeploy a container. The **credential deliberately is not here**: it
    # comes from SMTP_PASSWORD or RESEND_API_KEY in the environment, because a
    # secret in this table travels in all 45 retained nightly backups. See the
    # header of `mailer.py`.
    ("email_transport", "none", "email_transport", "Email delivery",
     "How this app sends mail. Needed for self-service password reset. "
     "SMTP works with any mail relay, including Microsoft 365, Google "
     "Workspace and Resend; the Resend option uses their web API instead, for "
     "hosts that will not allow an outbound SMTP connection.", "Email", 1, 200),
    ("email_from_address", "", "string", "From address",
     "The address mail appears to come from. Most relays will only accept an "
     "address on a domain they handle.", "Email", 1, 210),
    ("email_from_name", "", "string", "From name",
     "Shown beside the address in a mail client, e.g. Data Lineage Tracker. "
     "Optional.", "Email", 1, 220),
    ("app_base_url", "", "string", "Public address of this app",
     "e.g. https://lineage.example.gov \u2014 used to build the link in a "
     "password reset email. Required for reset email to work, and read from "
     "this setting rather than from the browser's request on purpose, so that "
     "a forged request cannot send somebody a reset link pointing somewhere "
     "else.", "Email", 1, 230),
    ("smtp_host", "", "string", "SMTP host",
     "e.g. smtp.example.gov. Only used when delivery is set to SMTP.",
     "Email", 1, 240),
    ("smtp_port", "587", "number", "SMTP port",
     "587 for STARTTLS, which is the usual choice; 465 for SSL.",
     "Email", 1, 250),
    ("smtp_security", "starttls", "smtp_security", "SMTP encryption",
     "STARTTLS upgrades a plain connection and is what most relays want on "
     "port 587. SSL is encrypted from the first byte, usually on port 465.",
     "Email", 1, 260),
    ("smtp_username", "", "string", "SMTP username",
     "The account used to authenticate. Its password comes from the "
     "SMTP_PASSWORD environment variable, never from this screen.",
     "Email", 1, 270),

    ("enable_self_service_reset", "false", "boolean",
     "Allow self-service password reset",
     "Lets anyone who has forgotten their password request a reset link by "
     "email. Requires email delivery above to be working \u2014 the link on the "
     "sign-in screen stays hidden until it is, so it cannot offer something "
     "that quietly fails.", "Email", 1, 280),

    # Phase 2 — the plumbing is in place; the flow is not built yet, so this
    # is shown but locked off.
    ("require_2fa", "false", "boolean", "Require two-factor authentication",
     "Not yet available.", "Coming soon", 0, 310),
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


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
# A small general-purpose alert mechanism. The first use is telling someone
# their change was reverted, but the shape is deliberately generic so later
# alerts can reuse it without another table.

def notify_user(conn, user_id, kind, title, body=None, detail=None,
                entity_type=None, entity_id=None, related_event_id=None,
                from_email=None):
    """Queue an in-app alert. Silently does nothing without a recipient."""
    if not user_id:
        return None
    row = conn.execute(
        """INSERT INTO notification
             (user_id, kind, title, body, detail_json, entity_type, entity_id,
              related_event_id, created_at, created_by_email)
           VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING notification_id""",
        (user_id, kind, title, body,
         json.dumps(detail, default=str) if detail else None,
         entity_type, str(entity_id) if entity_id is not None else None,
         related_event_id, iso(utcnow()), from_email)).fetchone()
    return row["notification_id"]


# ---------------------------------------------------------------------------
# Release notes
# ---------------------------------------------------------------------------
# Notes for versions that shipped before release tracking existed are seeded
# here so the "What's New" history isn't blank. Later releases are written in
# the app by a SuperAdmin.
#
# NOTE TO WHOEVER SHIPS THE NEXT RELEASE (human or AI assistant):
# see CLAUDE.md — draft the notes, then show Ben a preview and ask for edits
# BEFORE publishing.

SEED_RELEASES = [
    ("0.1.0", "Initial release", "2026-04-10", 1, 100, """\
## Data Lineage Tracker

* Interactive lineage diagram of systems and the data flows between them
* Catalogs for systems, data flows, and data elements
* Governance fields aligned to the Education Data Governance Framework
* Database schema drill-down for mapped data stores, with governance that can
  be set at the table and field level and inherits from the parent system
  unless overridden
"""),
    ("0.1.1", "PostgreSQL, Docker, and sign-in", "2026-08-05", 1, 200, """\
## Infrastructure and access control

* Moved from SQLite to **PostgreSQL**, so data is no longer lost when the
  service restarts
* Runs in Docker locally, matching the deployed environment
* Database schema changes now managed with versioned migrations
* **Sign-in with roles**: Viewer, Editor, Administrator, and SuperAdmin
* **Audit trail** recording every change field by field, plus sign-ins
* Preferences screen for global settings
* Renamed *Stakeholders* to **Contributors**
"""),
    ("0.2.0", "Releases, resources, and manual entry", None, 1, 300, """\
## What's new in this release

* **Version numbering and release notes** — the version appears in the corner
  of every page; click it to see what changed
* **Resources** on systems and data flows — attach documents or links with your
  own labels
* **Manual Entry** system type, for tracking data typed directly into a data
  store (replaces *Manual Process*)
* **Upload a .sql file** to refresh a data store's schema, keeping any
  governance you've already entered
* **Revert a change** from the audit log
* Data flows gained Organization, Managed By, and Data Steward, and an *Other*
  option for frequency where you can describe the timing in your own words
* Contributors now show by name rather than by ID
* Sortable tables, and a fix for the audit log appearing empty while loading
"""),
    ("0.2.1", "Resources, system types, and alerts", None, 1, 400, """\
## What's new in this release

**Resources are easier to read**

* Documents and links now show as a clickable title rather than a long file name
  or web address. Clicking a link opens it in a new tab; clicking a document
  offers to download it, and you can turn that prompt off.
* Each resource can have a short description.

**Improvements to System Types**

* *ETL Process* and *Internal* have been retired; systems that used them are
  now Data Stores.
* A SuperAdmin can edit the list of system types, their descriptions, and their
  colors in Preferences.
* The diagram key has an information button explaining what each type means.
* Choosing a type is now required when creating a system.

**Diagram**

* Sources are blue and destinations are green. Every color has been checked
  for contrast against accessibility standards.
* Click a data flow's number to open its details — an easier target than the line.
* With a details panel open, clicking the diagram closes it.
* The PII tag has been removed from the diagram; it still appears in the details
  panel and the catalogs.

**Elsewhere**

* Forms now say which required fields are missing instead of quietly refusing
  to save, and scroll back to the top so you can see what needs attention.
  This applies everywhere a record can be edited — the Add windows, the tables,
  and the details panel.
* Systems and data flows can be deleted from the details panel, after a
  confirmation. A system still used by a data flow is kept until the flow is
  removed first.
* Editors can upload a database schema; reverting one remains with SuperAdmins.
* Uploading a schema refreshes the diagram straight away.
* Ownership fields and the "Other" timing note can be filled in when a data flow
  is first created.
* System dropdowns are sorted by name.
* If someone reverts a change you made, you are told at your next sign-in.
"""),
    ("0.3.0", "Scenario planning", None, 1, 500, """\
## What's new in this release

**Scenario Planning**

* The live diagram is now called Current Architecture. Alongside users with edit
  permissions can create scenarios: private copies used to plan a change before
  anyone builds it. Nothing you do in a scenario affects Current Architecture.
* Start a scenario from Current Architecture, from another scenario, or from a
  blank page. Then add proposed systems and data flows, edit what is already
  there, move things around, or retire something.
* Scenarios are private to the people invited to them. You choose who can view
  and who can edit, and that choice applies only inside that scenario — a
  colleague who can only view elsewhere can be an editor here.
* Move a scenario through statuses: Draft, Under review, Approved and Archived.
  Only a Draft can be edited; the rest are read-only until returned to Draft.
* Hide the parts of the diagram a scenario is not focused on, to make it easier
  to read.
* Manage scenario button allows owner to easily modify name, status, and invite
  new collaborators after a scenario has already been created.

**Elsewhere**

* **Zoom controls** on the lineage diagram and the schema view: buttons to zoom
  in and out a step at a time, and the current percentage, which you can click
  to return to 100%.

**Fixes**

* Adding a data flow without choosing an owner no longer fails with an error.
* Secondary text throughout the app is darker, to meet accessibility contrast
  requirements.
"""),
    ("0.4.0", "Help when you need it", None, 1, 600, """\
## What's new in this release

**A help menu**

* There is now a **?** button next to the version number in the bottom corner.
  It opens a short set of help articles you can search by keyword.
* The panel knows which screen you are on and puts the relevant articles first,
  so opening it inside a scenario offers the scenario guides straight away.
* Articles cover the basics — what the application tracks, reading the lineage
  diagram, finding and editing records — as well as governance inheritance,
  database schemas, and a deep dive into how scenario planning works.
* If you have not explored **scenario planning** yet, the two scenario articles
  are the quickest way in.

**Fixes**

* Release notes and help articles now wrap properly instead of breaking
  sentences across lines.
"""),
    ("0.4.1", "Diagram controls and fixes", None, 1, 700, """\
## What's new in this release

**Arrows you can arrange**

* **Click an arrow's line** to adjust it. Connection points appear on the two
  systems it joins, and each end of the arrow becomes a grab handle.
* **Drag an end** to a different connection point, or click a point to attach
  it there. Systems stay put while an arrow is being adjusted.
* **Click the line again** to add a bend, then drag it. Double-click a bend to
  remove it.
* **Click an arrow's label** to open the data flow itself, as before.
* Two flows between the same pair of systems now separate themselves
  automatically, so a bidirectional pair recorded as two flows can be read as
  two flows.
* Where lines cross, one hops over the other so it is clear they do not meet.
* **Reset arrow** in a flow's details returns it to automatic routing.
* A data flow can be given a short **Display Name**, shown on the diagram in
  place of its identifier. Up to 20 characters; leave it blank to keep the ID.

**Field lists**

* A data flow can now carry a **Field List**, alongside documents and links, for
  the fields that flow carries.
* An attached document can be **cleared** without removing the slot it sits in —
  useful for a Manual Entry system's field list, which cannot be deleted because
  it is required. Earlier versions are kept.
* A field list added to a data flow can also be **removed** outright, since it
  is optional there.

**Fixes**

* Adding a Manual Entry system inside a scenario no longer fails.
* Attaching a document, or uploading a database schema, inside a scenario no
  longer reports that the record cannot be found.
* Transfer Method and Format are no longer required when editing a data flow —
  the form that creates one never asked for them.
* When creating a scenario, the systems offered under "focus on particular
  systems" are now those in whatever you chose to start from.
* Branching a scenario from another scenario keeps proposed, modified and
  retired items in those states instead of resetting them.
* **Proposed** can now be changed from a record's details, not only when it is
  first created.
"""),
    ("0.4.2", "Your own logo", None, 1, 800, """\
## What's new in this release

**A logo of your own**

* A SuperAdmin can upload a logo in **Preferences**, under General. It appears
  in the sidebar and on the sign-in screen.

**Adding a data flow is quicker**

* **Source System** and **Destination System** now come first when adding a
  data flow, and the name is filled in for you by default. Type over it
  whenever you want something different.
* The same applies when editing a flow: while the name is still the generated
  one, changing either system keeps it in step. A name you have written
  yourself is never overwritten.

**Fixes**

* **A stray click no longer discards what you were typing.** The Add and Edit
  windows used to close if you clicked beside them, losing everything entered.
  They now close only from the **X**, **Cancel**, or saving.
* The Source and Destination dropdowns in a data flow's details ran off the
  edge of the panel.
"""),
    ("0.4.3", "Reliability, and a note on what this is for", None, 1, 900, """\
## What's new in this release

**An introduction in the help panel**

* A new **About this software** article opens the help panel, covering what the
  tracker is for, who it is being built for, how it is run, and what it makes
  possible — from clearer vendor contracts to knowing who to ask when a figure
  looks wrong. It sits at the top of the list on every screen.

**Behind the scenes**

* **The application no longer goes to sleep.** It used to shut down after a
  quiet period and take up to a minute to wake and bring you to the login
  screen. It now stays running, and a cold visit is immediate.
* **The database is backed up every night to encrypted off-site storage**, kept
  separately from the application itself. Thirty days of daily copies are held,
  alongside monthly and annual ones. Restoring from those backups has been
  tested rather than assumed.
* Updates are now applied without taking the application offline.
"""),
    ("0.4.4", "Retiring, editing, and clearer buttons", None, 1, 1000, """\
## What's new in this release

**Retiring something in a scenario**

* A scenario can now show that a plan **takes a system or data flow out of
  service**, rather than only that it adds one. Open the record and choose
  **Retire in this scenario**: it stays on the diagram, faded and struck
  through, so the plan shows what goes as well as what arrives.
* Nothing is deleted, Current Architecture is untouched, and you can undo it
  with **Not retired after all**.
* **Delete** still works exactly as before, for something the scenario
  invented and no longer wants. Retiring is refused for those, since there is
  nothing to retire from — the message says so and points at Delete.

**Editing a document or link**

* Resources have an **Edit** button. Correcting a title, a description or a web
  address no longer means deleting the entry and adding it again, which used to
  throw away its version history.
* **Clear** and **Remove** now explain the difference. Clear empties an entry
  but keeps it, which matters for a Manual Entry system's field list because
  that one is required. Remove deletes the entry outright.

**Tooltips that appear when you point at them**

* Explanatory tooltips appear promptly, anywhere on the control rather than one
  exact spot, and can be read by keyboard as well as mouse.
* Several buttons that should have had an explanation and silently did not —
  **Remove** among them — now have one.

**Elsewhere**

* The name of a data flow no longer covers its own arrowhead when the flow is
  short and points upward.
* Creating a scenario from another scenario and choosing to **invite the same
  people** now actually invites them, with the roles they already had. The box
  had never worked.
* The systems listed under **focus on particular systems** are sorted by name.
* **Deprecated** has been removed as a status. Nothing was using it, and
  retiring in a scenario is now the way to express that something is going
  away.
"""),
    ("0.5.0", "Say what a field means", None, 1, 1100, """\
## What's new in this release

**Definitions for fields**

* A SuperAdmin can write a short definition for any field on a system, data
  flow, data element or contributor — in **Preferences**, under General.
* Where a definition exists, an **information marker** appears beside that
  field in the details panel, for everyone. Point at it, or reach it with the
  Tab key, to read the definition.
* Pick a field from a list that shows where it lives — *Data Flow → Transfer
  Details → Frequency*. A check marks the ones already done, and the count
  shows how far along you are, which makes the gaps visible.
* Some fields are labeled slightly differently in different places — a
  classification is "Classification" in one view and "Data Classification" in
  another. **One definition covers every phrasing**, and Preferences tells you
  when a field has more than one.

**Fields that appear in more than one place**

* Eleven fields exist on several kinds of record — **Data Steward** is on
  systems, data flows and data elements. For those there is a check box: **use
  one definition everywhere this field appears**, checked to begin with. Change
  the wording once and all of them update.
* Leave the box clear where the meaning really differs: a system's
  *Description* says what the system is for, a data flow's says what the flow
  carries.
* A record type can also keep its own wording that overrides the shared one,
  and removing that override brings the shared wording back.

**Still to come**

* Definitions appear in the details panel in this release. The **Add** windows
  follow in the next one, along with the ability to **hide fields** that your
  organization does not use.
"""),
    ("0.5.1", "Only the fields you use", None, 1, 1200, """\
## What's new in this release

**Switch off the fields you do not use**

* In **Preferences**, under General, a SuperAdmin can now mark a field as not
  used, which hides the field in both the details panel and the **Add** window.
* Set for one record type at a time. A department might record *Data Standards*
  on data flows and never on systems, so turning it off is not shared across
  record types, even when definitions might be.
* **Nothing is deleted.** Anything already recorded in a field you switch off
  is kept, and comes back exactly as it was if you switch the field on again.
* Fields that are **required** when creating a record cannot be switched off —
  the form could not be completed without them. Those show the reason rather
  than simply omitting the control.

**Definitions in the Add windows**

* A definition written in Preferences now appears while you are *creating* a
  record, not only when editing one afterwards.
"""),
    ("0.5.2", "Names, not identifiers", None, 1, 1300, """\
## What's new in this release

**Fixes**

* **Owner** on a data flow can be changed again — choosing one and saving used
  to appear to work and do nothing.
* **Source of Truth** on a data element is now chosen from a list of systems,
  so what you pick is what gets saved.

**People**

* **Email address now appears for Owners and Data Stewards**, so they can more
  easily be contacted with questions.
* **Contributors can be deactivated** when someone is no longer involved, so
  the list you choose from stays current. Records that already name them keep
  the assignment, marked *deactivated*, so nothing quietly loses its steward.

**Deleting**

* **Deleting a system or data flow now says what goes with it** — the schema
  tables, columns and documents attached to it — so nothing large disappears
  by surprise. A system with an uploaded schema asks you to type its name.
* A system still used by data flows now **names those flows**, instead of
  refusing without saying why.

**Data flows**

* New **Manual Steps** check box records that a flow depends on a person, so
  the places where timing can slip are easy to find. Sortable in the data
  flows table.

**Smaller things**

* **PII Elements** removed from the bar across the top of a view.
* The knowledge base now explains **which changes can be reverted** from the
  audit log, and which cannot.
* Confirmation windows no longer close when you click outside them.
"""),
    ("0.6.0", "Your own account", None, 1, 1400, """\
## What's new in this release

**Your own account**

* Your name now sits in the **top right**, with a menu holding **Your profile**
  and **Sign out**. It used to be at the foot of the sidebar.
* You can change your own **name, title and password** from there, without
  asking an administrator. Changing your password signs out your other
  sessions.
* Your **email and role** are shown but still set by an administrator, since
  your email is how you sign in and how your changes are recorded.

**Titles**

* A user account can now carry a **title**, shown wherever that person is
  named as a contributor, so a name on a record says what the person does.
* An administrator can set it too, on the Users screen.

**Adding a contributor who already has an account**

* **Add Contributor** now lets you pick someone from a list of accounts, which
  fills in their name and attaches the two records together — no retyping, and
  nothing to get wrong.
* Their email and title stay with their account rather than being copied, so
  the two can never drift apart.
* Anyone who already has a contributor record is grayed out in the list, with
  the record they belong to named.

**Layout**

* **Add System**, **Add Flow** and **Add Contributor** have moved down beside
  the record counts, which is where the space freed up by the account menu
  went.
"""),
    ("0.6.1", "Tidier diagrams", None, 1, 1500, """\
## What's new in this release

**Lining things up**

* Dragging a system now shows a **dotted guide** when it lines up with another
  one, and settles onto it, so boxes can be aligned without eyeballing them.
* Dragging a bend in an arrow **snaps to the nearest straight or diagonal**
  when it is close, so lines look deliberate rather than nearly right.
* Hold **Shift** while dragging to turn both off, for the times you want
  something slightly off square.

**Sharing a diagram**

* **Export** on the lineage view offers the diagram as **SVG, PNG, JPG or
  PDF**, with the file name ready to edit, so it can go into a slide, a
  document or a printed page without being screenshotted.
* The file holds the **whole diagram**, not just the part you can see, and is
  headed with the view it came from.
"""),
    # Published 4 November 2026, after Ben reviewed and rewrote the wording.
    # Published notes are never overwritten by `ensure_releases`, so from here
    # on an administrator's edits in the app survive restarts and deploys.
    # Pinned to the literal version rather than VERSION: the entry is
    # published, and leaving it keyed on VERSION would make the next bump
    # insert these same notes under the new number, published, and show the
    # Tasks announcement to everyone a second time.
    ("0.7.0", "Tasks", None, 1, 1600, """\
## What's new in this release

**Tasks**

* New **Tasks** record type for the work a flow or a data store depends on —
  validation steps, a stored procedure, a file somebody downloads and uploads
  by hand.
* A task is attached to a data flow or data store, and includes a person
  responsible, status, and expected start and end dates.
* Subtasks can be added to a task, each with its own person responsible,
  status, and dates.
* A task's status is worked out from its subtasks.
* **Task Types** in Preferences — the four supplied are a starting point, and
  **Other** is always available with a box to say what the work actually is.
* Tasks appear on the **lineage diagram** as small circles on the flow or
  inside the system box — hover for the name and status, click to open,
  drag to reposition.
* Documents and links can be attached to a task, as they can to a system or a
  data flow.
* Tasks can be copied into or added in scenarios, as well.
"""),
    # Published 6 November 2026, in Ben's own wording. Published notes are
    # never overwritten by `ensure_releases`, so from here on an
    # administrator's edits in the app survive restarts and deploys.
    # Pinned once published, so the next bump cannot re-insert these notes
    # under a new number and show the announcement to everyone twice.
    ("0.7.1", "Searchable lists and task colors", None, 1, 1700, """\
## What's new in this release

**Fixes**

* Clicking a table or a field inside a schema no longer shows a blank screen.
* A task's Attached to list shows every system and data flow, so a task can be
  moved without recreating it.
* An expected end date can no longer be set before its start date on a task or
  a subtask.
* A task with no dates of its own shows the span its subtasks cover. Entering
  a date on the task overrides it.

**Searchable lists**

* Every dropdown listing people, systems, data flows, fields or task types can
  be typed into to narrow it, making it easier to find an entry in a long list.

**Tasks on the diagram**

* Task circles are color-coded by status and expected dates: completed, past
  its end date, in progress, not started past its start date, or nothing
  recorded yet.
* Color-coding looks at both the task and any subtask's dates and status.
* Each color also carries a mark inside the circle to support black and white
  viewing & printing.
* The legend at the bottom left will only show the task circle when a task has
  been included in the diagram, and it will only show the color-coding when
  dates and/or statuses have been added to tasks.
"""),
    # Reviewed and approved by Ben, 11 November 2026, so published. A published
    # release is never re-seeded: further wording changes are made in the app,
    # not here.
    ("0.8.0", "Task dependencies and automated tasks", None, 1, 1800, """\
## What's new in this release

**Tasks can wait on other tasks**

* A task can now list other tasks it depends on. Check **Add Dependencies**,
  in the Add Task window or on an existing task, and choose them.
* A task past its start date with an unfinished dependency is shown as
  **Blocked** on the diagram, and hovering names what it is waiting for.
* A task with no start date anywhere counts as ready to start once every
  dependency is completed, so a chain of work can be planned without putting a
  date on each step. Hovering reads "Not started, dependencies completed".
* A task cannot depend on itself, on a task in another view, or in a way that
  forms a loop.
* Unchecking **Add Dependencies** removes the dependencies, and asks first.

**Automated tasks**

* Check **Automated task** on work a machine does, and its circle turns blue,
  so the diagram shows at a glance which tasks need a person.
* The blue appears where there is nothing else to report: a task that is late
  or blocked is still shown that way.
* A copy of a task stays automated.

**Elsewhere**

* **Un-hide everything** in a scenario now asks first. There is no undo, and
  nothing records which items were hidden.
* A help article on tasks is in the help panel, covering subtasks, dates, the
  diagram colors, automated tasks and dependencies.
"""),
    # Reviewed and approved by Ben, November 2026, so published. From here it
    # is live and `ensure_releases` will never overwrite it — any further
    # change has to be made in the app, not in this file.
    #
    # Note the literal "0.9.0" rather than VERSION: this entry is pinned to the
    # release it describes. It used to read VERSION, which was correct only
    # while 0.9.0 was current — the next bump would have silently relabeled
    # these notes as belonging to the new version, and a published release is
    # never re-seeded, so the database would have disagreed with the code with
    # nothing to show why. Only the newest, still-draft entry should use
    # VERSION.
    ("0.9.0", "Password reset by email", None, 1, 1900, """\
## What's new in this release

**Forgotten passwords**

* Anyone who has forgotten their password can now request a reset link by
  email, instead of asking an administrator for a temporary one.
* The link works once and stops working after an hour.
* Setting a new password this way signs the account out everywhere else.
* Requesting a reset answers the same way whether or not the address has an
  account here, so nothing arriving may simply mean a mistyped address.

**Setting up email**

* **Preferences \u2192 Email** holds the mail settings: SMTP for your own relay,
  or Resend if an outbound SMTP connection is not available.
* The password or API key is set in the server's environment rather than on
  this screen, so it is never stored in the database or carried in a backup.
* **Email status** says whether the credential was found and has a **Send test
  message** button that reports exactly what went wrong.
* The link on the sign-in screen stays hidden until email is working, so it
  cannot offer something that quietly fails.
"""),
    # Reviewed and approved by Ben, November 2026, so published. Pinned to the
    # literal version rather than VERSION for the reason given above: the next
    # bump would otherwise relabel these notes as belonging to the new
    # release, and a published release is never re-seeded.
    ("0.9.1", "Scenario focus, and email status", None, 1, 2000, """\
## Fixes

* Creating a scenario while another one was open offered **that** scenario's
  systems to focus on, whichever starting point you had chosen. It now offers
  the systems belonging to what you picked.
* **Email status** updates when you save the settings above it, rather than
  waiting for a page reload — so **Send test message** becomes available as
  soon as the configuration is complete.
"""),
]


def get_system_types(conn=None):
    """The configured system types, in display order."""
    own = conn is None
    conn = conn or get_db()
    rows = conn.execute(
        "SELECT * FROM system_type ORDER BY sort_order, type_name").fetchall()
    if own:
        conn.close()
    return rows_to_list(rows)


def get_task_types(conn=None):
    """The configured task types, in display order."""
    own = conn is None
    conn = conn or get_db()
    rows = conn.execute(
        "SELECT * FROM task_type ORDER BY sort_order, type_name").fetchall()
    if own:
        conn.close()
    return rows_to_list(rows)



# Definitions shipped with the app, for fields whose name genuinely does not
# carry their meaning. Seeded once and then left alone: an administrator can
# reword or remove one in Preferences and that edit survives every restart,
# which is why this is a seed rather than something re-applied on boot.
#
# Keep this list short. A definition is worth shipping when the field would
# otherwise be misread as a different field — not merely because it could use
# some explanation.
SEED_FIELD_DEFINITIONS = [
    ("data_flow", "manual_steps",
     "Manual steps means there is human intervention at some point in the "
     "data flow (e.g., reviewing data and approving it before flow occurs, "
     "manually uploading a file to a location, or some other step that has "
     "not been automated). Manual steps (as a flag on a data flow) differs "
     "from Manual entry (as a system type). Manual entry is for creation of "
     "data by entering it directly into a system (either by uploading a "
     "spreadsheet the user has filled in manually, or by entering data "
     "directly into a screen), rather than moving data between systems."),
]


# The starting set of task types. Kept here rather than in migration 0016 so
# the list exists once: the test harness builds its schema from the migration
# files and ignores anything that is not DDL, which is why the system-type list
# ended up written twice — once in 0004 and once by hand in harness_setup.py.
#
# A starting point, not a taxonomy. Ben named these four; the whole reason task
# types are a table is that the list belongs to whoever is using the app.
SEED_TASK_TYPES = [
    ("Validation", "A set of checks run against the data before it is accepted, "
     "usually as a numbered list of steps.", 10),
    ("Stored Procedure", "Code run against a database on a schedule or on "
     "request, rather than as part of an automated transfer.", 20),
    ("Manual Transfer", "A file somebody downloads, uploads, or moves by hand "
     "between two places.", 30),
    ("Review and Approval", "A person checks the data and signs off before it "
     "moves on.", 40),
]


def ensure_task_types(conn=None):
    """Fill task_type on a fresh database, and never again.

    Seeded only when the table is **entirely empty**, unlike
    `ensure_field_definitions` which checks row by row. The difference matters:
    a definition an administrator removes is a gap worth refilling with the
    shipped wording, but a task type they delete is a decision, and bringing it
    back at the next restart would look like the delete had failed.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        if conn.execute("SELECT COUNT(*) FROM task_type").fetchone()[0]:
            return
        now = iso(utcnow())
        for name, desc, order in SEED_TASK_TYPES:
            conn.execute(
                """INSERT INTO task_type
                     (type_name, description, sort_order, created_date, updated_date)
                   VALUES (?,?,?,?,?)""",
                (name, desc, order, now, now))
        conn.commit()
        print(f"Task types: seeded {len(SEED_TASK_TYPES)}", flush=True)
    finally:
        if own:
            conn.close()


def ensure_field_definitions(conn=None):
    """Insert the shipped definitions, once, without disturbing edits.

    Unlike `ensure_releases`, this never updates an existing row. A release
    note that is still a draft is owned by the code; a field definition is
    owned by whoever wrote it in Preferences the moment they touch it, and
    re-applying the shipped wording on every restart would silently undo them.
    """
    own = conn is None
    conn = conn or get_db()
    added = 0
    try:
        for entity, column, text in SEED_FIELD_DEFINITIONS:
            existing = conn.execute(
                "SELECT 1 FROM field_definition "
                "WHERE entity_type=? AND column_name=?", (entity, column)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """INSERT INTO field_definition
                     (entity_type, column_name, definition, updated_date)
                   VALUES (?,?,?,?)""",
                (entity, column, text, iso(utcnow())))
            added += 1
        if added:
            conn.commit()
            print(f"Field definitions: seeded {added}", flush=True)
    finally:
        if own:
            conn.close()

def ensure_releases(conn=None):
    """Insert release notes for versions that don't have an entry yet."""
    own = conn is None
    conn = conn or get_db()
    now = iso(utcnow())
    existing = {r["version"]: r["is_published"] for r in
                conn.execute(
                    "SELECT version, is_published FROM app_release").fetchall()}
    added = 0
    refreshed = 0
    for ver, title, released, published, sort_key, notes in SEED_RELEASES:
        if ver in existing:
            # A release that is still a draft is owned by the code: re-seed it so
            # wording changes reach every environment, and so approving a draft
            # here is what publishes it. A release that has already been
            # published is live and is never overwritten — an administrator may
            # have edited it in the app, and those edits must survive a restart.
            if not existing[ver]:
                conn.execute(
                    """UPDATE app_release
                          SET title=?, notes=?, is_published=?, sort_key=?,
                              updated_date=?
                        WHERE version=? AND is_published=0""",
                    (title, notes, published, sort_key, now, ver))
                refreshed += 1
            continue
        conn.execute(
            """INSERT INTO app_release
                 (version, title, notes, released_date, is_published, sort_key,
                  created_date, updated_date)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ver, title, notes, released or now[:10], published, sort_key, now, now))
        added += 1
    # Any account without a marker is treated as up to date, so nobody is shown
    # a backlog of releases from before they had an account. Without this, a NULL
    # would also suppress every FUTURE release for that user.
    latest = conn.execute(
        "SELECT version FROM app_release WHERE is_published=1 "
        "ORDER BY sort_key DESC LIMIT 1").fetchone()
    if latest:
        conn.execute("UPDATE app_user SET last_seen_release=? "
                     "WHERE last_seen_release IS NULL", (latest["version"],))
    conn.commit()
    if own:
        conn.close()
    if added:
        print(f"Release notes: seeded {added} version(s)", flush=True)
    if refreshed:
        print(f"Release notes: refreshed {refreshed} draft(s) from the code",
              flush=True)


def reset_superadmin_if_requested(conn):
    """Emergency password reset for a SuperAdmin, driven by environment variables.

    Setting BOOTSTRAP_SUPERADMIN_RESET=true together with
    BOOTSTRAP_SUPERADMIN_EMAIL and BOOTSTRAP_SUPERADMIN_PASSWORD resets that
    account's password on the next start and forces a change at the next
    sign-in.

    This was written as the only way back in before self-service reset existed,
    and that is no longer the reason to keep it. Self-service reset (0.9.0)
    covers a forgotten password, but it needs working mail, a public address
    and the preference switched on — so it cannot help the deployment where
    mail was never configured, where it has broken, or where the last
    SuperAdmin's mailbox is itself unreachable. This is the floor under that:
    it needs nothing but the ability to set an environment variable.

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
                  SET password_hash=?, must_change_password=?, is_active=1,
                      role=?, updated_date=?
                WHERE user_id=?""",
            (auth.hash_password(password),
             0 if skip_forced_password_change() else 1,
             permissions.SUPERADMIN, now, row["user_id"]))
        conn.execute("UPDATE user_session SET revoked_at=? WHERE user_id=? "
                     "AND revoked_at IS NULL", (now, row["user_id"]))
        user_id = row["user_id"]
        what = "reset"
    else:
        created = conn.execute(
            """INSERT INTO app_user
                 (email, first_name, last_name, password_hash, role, is_active,
                  must_change_password, created_date, updated_date)
               VALUES (?,?,?,?,?,1,?,?,?)
               RETURNING user_id""",
            (email,
             os.environ.get("BOOTSTRAP_SUPERADMIN_FIRST_NAME", "System"),
             os.environ.get("BOOTSTRAP_SUPERADMIN_LAST_NAME", "Administrator"),
             auth.hash_password(password), permissions.SUPERADMIN,
             0 if skip_forced_password_change() else 1, now, now)).fetchone()
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


_SKIP_WARNED = False


def _warn_skip_enabled():
    """Say loudly, once, that the forced password change is switched off."""
    global _SKIP_WARNED
    _SKIP_WARNED = True
    where = os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID")
    print("\n  BOOTSTRAP_SUPERADMIN_SKIP_PASSWORD_CHANGE is on: the SuperAdmin "
          "will NOT be\n  asked to change the password from the environment "
          "variable.", flush=True)
    if where:
        print("  This looks like a deployed environment. Remove this variable "
              "unless you\n  are certain you want it.\n", flush=True)
    else:
        print("  Intended for local development only.\n", flush=True)


def skip_forced_password_change():
    """Whether a bootstrapped or reset SuperAdmin may keep its given password.

    Off by default, and it should stay off anywhere real. A temporary password
    typed into an environment variable has been seen by whoever set it and may
    sit in a shell history or a `.env` file, which is why the normal behavior
    is to force a change at first sign-in.

    On a local machine, where the database is routinely thrown away and
    recreated, that prompt appears every time and protects nothing — the
    password is already in `.env` on the same disk. Setting
    ``BOOTSTRAP_SUPERADMIN_SKIP_PASSWORD_CHANGE=true`` there is a reasonable
    trade; setting it on a deployed environment is not.
    """
    value = str(os.environ.get("BOOTSTRAP_SUPERADMIN_SKIP_PASSWORD_CHANGE", "")) \
        .strip().lower()
    on = value in ("1", "true", "yes", "on")
    if on and not _SKIP_WARNED:
        _warn_skip_enabled()
    return on


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
               VALUES (?,?,?,?,?,1,?,?,?)
               RETURNING user_id""",
            (email,
             os.environ.get("BOOTSTRAP_SUPERADMIN_FIRST_NAME", "System"),
             os.environ.get("BOOTSTRAP_SUPERADMIN_LAST_NAME", "Administrator"),
             auth.hash_password(password), permissions.SUPERADMIN,
             0 if skip_forced_password_change() else 1, now, now),
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
        # Whether this endpoint understands ``?scenario=N``. Off by default so
        # an endpoint that has not been scoped cannot be handed a scenario and
        # silently operate on live data instead.
        SCENARIO_AWARE = False

        def initialize(self):
            self.current_session = None
            self.current_user = None
            self.scenario_id = None

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

        def write_error(self, status_code, **kwargs):
            """Return JSON for API errors, never Tornado's HTML page.

            An HTML body reaches the frontend as "Unexpected token '<'", which
            hides the real status. This keeps every failure legible.
            """
            reason = self._reason or "Request failed"
            detail = None
            exc = kwargs.get("exc_info")
            if exc and not isinstance(exc[1], tornado.web.HTTPError):
                # Log the traceback server-side; don't leak internals to the client.
                import traceback
                traceback.print_exception(*exc)
                detail = f"{exc[0].__name__}: {exc[1]}"
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({
                "error": f"{status_code}: {reason}",
                "detail": detail,
                "status": status_code,
            }))

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
                              u.email, u.first_name, u.last_name, u.title,
                              u.role, u.is_active, u.must_change_password,
                              u.contributor_id
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

                # Built explicitly rather than from the row, so a column
                # added to the join does not silently become part of what the
                # browser is handed. `title` is here because the account chip
                # and the profile window both show it.
                user = {
                    "user_id": data["user_id"], "email": data["email"],
                    "first_name": data["first_name"], "last_name": data["last_name"],
                    "title": data["title"],
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

            # Which view is this request about? NULL/absent means the live
            # diagram; anything else is a scenario.
            self.scenario_id = None
            if self.SCENARIO_AWARE:
                raw = self.get_argument("scenario", None)
                if raw not in (None, "", "live"):
                    try:
                        self.scenario_id = int(raw)
                    except ValueError:
                        return self.fail(404, "Scenario not found.")

            required = self.READ_ROLE if method in self.SAFE_METHODS else self.WRITE_ROLE
            if self.scenario_id is not None and method not in self.SAFE_METHODS:
                # Inside a scenario, authority comes from scenario membership
                # rather than the system role — that is the whole point of
                # being able to invite a Viewer as an editor. The check is not
                # skipped, it moves: every scenario write calls
                # ``access(...).require_write()`` before touching anything, and
                # a non-member gets a 404 there. Live writes are unaffected,
                # because this branch needs an explicit scenario id.
                required = permissions.VIEWER
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

        # -- scenario helpers -------------------------------------------------
        def scenario_access(self, conn, write=False, manage=False):
            """Resolve this request's rights, raising the right HTTP error.

            Call this before reading or writing anything scoped. It returns an
            ``Access`` whose ``scenario_id`` is what every query must scope on.
            """
            acc = scenarios.access(conn, self.current_user, self.scenario_id)
            if manage:
                acc.require_manage()
            elif write:
                acc.require_write()
            else:
                acc.require_read()
            return acc

        def scope_sql(self, alias=None, table=None, respect_hidden=False):
            """``(clause, params)`` restricting a query to this request's view.

            ``respect_hidden`` additionally drops records the scenario has
            tidied away, unless the caller asked to see everything. Reads that
            feed the diagram and the catalogs pass it; anything that must
            consider the whole scenario deliberately does not.
            """
            if table in scenarios.COPY_ON_WRITE_TABLES:
                clause, params = scenarios.visible_scope(
                    self.scenario_id, alias, table)
            else:
                clause, params = scenarios.scope(self.scenario_id, alias)
            if respect_hidden and self.scenario_id is not None:
                extra, _ = scenarios.hidden_clause(self.include_hidden, alias)
                clause += extra
            return clause, params

        @property
        def include_hidden(self):
            """Whether this request asked to see hidden records too."""
            return self.get_argument("include_hidden", "") in ("1", "true", "yes")

        # -- auditing helpers -------------------------------------------------
        def audit_create(self, conn, entity_type, entity_id, values):
            return audit.record_create(
                conn, self.current_user, entity_type, entity_id, values,
                scenario_id=self.scenario_id,
                ip_address=self.client_ip(), user_agent=self.user_agent())

        def audit_update(self, conn, entity_type, entity_id, before, after,
                         fields=None):
            return audit.record_update(
                conn, self.current_user, entity_type, entity_id, before, after,
                fields=fields, scenario_id=self.scenario_id,
                ip_address=self.client_ip(),
                user_agent=self.user_agent())

        def audit_delete(self, conn, entity_type, entity_id, before):
            return audit.record_delete(
                conn, self.current_user, entity_type, entity_id, before,
                scenario_id=self.scenario_id,
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
        SCENARIO_AWARE = True

        def get(self):
            conn = get_db()
            try:
                self.scenario_access(conn)
                where, params = self.scope_sql("s", respect_hidden=True)
                rows = conn.execute(
                    f"""SELECT s.*,
                              (SELECT COUNT(*) FROM schema_table st
                               WHERE st.system_id = COALESCE(
                                 (SELECT s2.system_id FROM schema_table s2
                                   WHERE s2.system_id = s.system_id LIMIT 1),
                                 s.origin_id, s.system_id)) AS schema_table_count
                       FROM system s WHERE {where} ORDER BY s.system_id""",
                    params).fetchall()
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            conn.close()
            self.write_json(rows_to_list(rows))

        def post(self):
            data = self.get_json_body()
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            complaint = reject_proposed_on_live(self, data)
            if complaint:
                conn.close()
                return self.fail(400, complaint)
            # Auto-generate ID, numbered within this view so a scenario cannot
            # collide with live or with another scenario.
            where, params = self.scope_sql("s")
            last = conn.execute(
                f"SELECT system_id FROM system s WHERE {where} "
                "ORDER BY system_id DESC LIMIT 1", params).fetchone()
            if last:
                num = int(re.search(r'\d+',
                                    scenarios.base_id(last['system_id'])).group()) + 1
            else:
                num = 1
            sid = scenarios.scoped_id(self.scenario_id, f"SYS-{num:03d}")
            # The type is required and must be one that exists; there is no
            # default, so a missing type is a client error rather than a guess.
            stype = (data.get('system_type') or '').strip()
            valid = {t["type_name"] for t in get_system_types(conn)}
            if not stype:
                conn.close()
                return self.fail(400, "A system type is required.")
            if stype not in valid:
                conn.close()
                return self.fail(400, f"'{stype}' is not a known system type.")
            now = datetime.datetime.now().isoformat()
            conn.execute("""INSERT INTO system (system_id,name,description,system_type,vendor,technology,
                environment,managed_by,owner_id,organization,contains_pii,ferpa_protected,status,notes,
                x_pos,y_pos,
                data_classification,access_method,encryption_at_rest,encryption_in_transit,
                last_security_review,has_audit_trail,
                data_steward_id,retention_schedule,destruction_method,backup_frequency,
                disaster_recovery,data_standards,
                authorized_users,data_sharing_restrictions,
                entry_type,entered_by,entry_validation,entry_error_correction,target_system_id,
                created_date,updated_date,scenario_id,change_state)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, data.get('name',''), data.get('description',''), stype,
                 data.get('vendor',''), data.get('technology',''), data.get('environment','Production'),
                 data.get('managed_by',''), fk(data.get('owner_id')), data.get('organization',''),
                 data.get('contains_pii',0), data.get('ferpa_protected',0), data.get('status','Active'),
                 data.get('notes',''), data.get('x_pos',400), data.get('y_pos',300),
                 data.get('data_classification'), data.get('access_method'),
                 data.get('encryption_at_rest'), data.get('encryption_in_transit'),
                 data.get('last_security_review'), data.get('has_audit_trail',0),
                 fk(data.get('data_steward_id')), data.get('retention_schedule'),
                 data.get('destruction_method'), data.get('backup_frequency'),
                 data.get('disaster_recovery'), data.get('data_standards'),
                 data.get('authorized_users'), data.get('data_sharing_restrictions'),
                 data.get('entry_type'), data.get('entered_by'),
                 data.get('entry_validation'), data.get('entry_error_correction'),
                 fk(data.get('target_system_id')),
                 now, now, self.scenario_id,
                 change_state_for_new(self, data)))
            self.stamp_author(conn, "system", "system_id", sid, creating=True)
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            self.audit_create(conn, "system", sid, dict(row))
            if row["system_type"] == MANUAL_ENTRY:
                ensure_field_list_resource(self, conn, sid)
                sync_manual_entry(self, conn, sid, data, creating=True)
            conn.commit()
            conn.close()
            self.set_status(201)
            self.write_json(row_to_dict(row))

    class SystemHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

        def get(self, sid):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            # Scoping the lookup, rather than fetching by id alone, is what
            # stops someone in one scenario reading a record from another by
            # guessing its identifier.
            where, params = self.scope_sql("s")
            row = conn.execute(
                f"""SELECT s.*,
                          (SELECT COUNT(*) FROM schema_table st
                           WHERE st.system_id = COALESCE(
                             (SELECT s2.system_id FROM schema_table s2
                               WHERE s2.system_id = s.system_id LIMIT 1),
                             s.origin_id, s.system_id)) AS schema_table_count
                   FROM system s WHERE s.system_id=? AND {where}""",
                (sid,) + params).fetchone()
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
                        'authorized_users','data_sharing_restrictions',
                        # Manual Entry
                        'entry_type','entered_by','entry_validation',
                        'entry_error_correction','target_system_id')

        def put(self, sid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            complaint = reject_proposed_on_live(self, data)
            if complaint:
                conn.close()
                return self.fail(400, complaint)
            where, params = self.scope_sql()
            before = conn.execute(
                f"SELECT * FROM system WHERE system_id=? AND {where}",
                (sid,) + params).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            gaps = missing_required("system", data, before)
            if gaps:
                conn.close()
                return self.fail(400, f"These fields are required: {', '.join(gaps)}.",
                                 missing=gaps)
            fields = []
            vals = []
            for key in self.SYSTEM_FIELDS:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(fk(data[key]) if key in FK_FIELDS else data[key])
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(sid)
                conn.execute(f"UPDATE system SET {','.join(fields)} WHERE system_id=?", vals)
            if "proposed" in data and self.scenario_id is not None:
                # Never overwrite "removed" from here. The Proposed checkbox
                # wrote change_state unconditionally, so saving a retired
                # record with the box clear quietly un-retired it, and with
                # the box checked claimed a live system was invented here.
                # Retiring has its own action; this only moves between
                # proposed and unchanged.
                conn.execute(
                    "UPDATE system SET change_state=? "
                    "WHERE system_id=? AND scenario_id=? AND change_state!=?",
                    (scenarios.PROPOSED if data["proposed"] else scenarios.UNCHANGED,
                     sid, self.scenario_id, scenarios.REMOVED))
            elif fields:
                mark_modified(conn, "system", "system_id", sid, self.scenario_id)
            row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
            # Position changes are excluded from the audit: dragging a node is a
            # layout preference, and logging it would bury real governance edits.
            self.audit_update(conn, "system", sid, before, dict(row),
                              fields=[f for f in self.SYSTEM_FIELDS
                                      if f not in ("x_pos", "y_pos")])
            if row["system_type"] == MANUAL_ENTRY:
                ensure_field_list_resource(self, conn, sid)
                sync_manual_entry(self, conn, sid, data)
            conn.commit()
            conn.close()
            self.write_json(row_to_dict(row))

        def delete(self, sid):
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            where, params = self.scope_sql()
            before = conn.execute(
                f"SELECT * FROM system WHERE system_id=? AND {where}",
                (sid,) + params).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")

            # Inside a scenario, retiring something that exists today is a
            # different statement from deleting something you invented here.
            # "Removed" keeps it on the diagram, struck through, so the plan
            # reads as "we retire these, we add those" rather than as a gap.
            mode = self.get_argument("mode", "")
            if self.scenario_id is not None and mode in ("remove", "restore"):
                try:
                    state = scenarios.set_retired(
                        conn, "system", "system_id", sid, self.scenario_id,
                        retired=(mode == "remove"))
                except scenarios.RetireRefused as e:
                    conn.close()
                    return self.fail(400, str(e))
                self.audit_update(conn, "system", sid, dict(before),
                                  {**dict(before), "change_state": state},
                                  fields=["change_state"])
                conn.commit()
                conn.close()
                return self.write_json({"system_id": sid, "change_state": state})

            flow_where, flow_params = self.scope_sql()
            flows = conn.execute(
                f"SELECT COUNT(*) FROM data_flow WHERE (source_system_id=? "
                f"OR destination_system_id=?) AND {flow_where}",
                (sid, sid) + flow_params).fetchone()[0]
            if flows:
                conn.close()
                return self.fail(409, f"This system is used by {flows} data flow(s). "
                                      f"Delete or repoint those flows first.")
            conn.execute("DELETE FROM schema_column WHERE table_id IN "
                         "(SELECT table_id FROM schema_table WHERE system_id=?)", (sid,))
            conn.execute("DELETE FROM schema_snapshot WHERE system_id=?", (sid,))
            conn.execute("DELETE FROM schema_table WHERE system_id=?", (sid,))
            conn.execute("DELETE FROM resource_version WHERE resource_id IN "
                         "(SELECT resource_id FROM resource "
                         " WHERE entity_type='system' AND entity_id=?)", (sid,))
            conn.execute("DELETE FROM resource WHERE entity_type='system' AND entity_id=?",
                         (sid,))
            delete_tasks_for(conn, "system", sid)
            conn.execute("DELETE FROM system WHERE system_id=?", (sid,))
            self.audit_delete(conn, "system", sid, dict(before))
            conn.commit()
            conn.close()
            self.write_json({"deleted": sid})

    # -- Data Flows --
    class FlowsHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

        def get(self):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            where, params = self.scope_sql("f", respect_hidden=True)
            rows = conn.execute(
                f"""SELECT f.*, s1.name as source_name, s2.name as dest_name
                FROM data_flow f
                JOIN system s1 ON f.source_system_id=s1.system_id
                JOIN system s2 ON f.destination_system_id=s2.system_id
                WHERE {where}
                ORDER BY f.flow_id""", params).fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

        def post(self):
            data = self.get_json_body()
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            complaint = reject_proposed_on_live(self, data)
            if complaint:
                conn.close()
                return self.fail(400, complaint)
            where, params = self.scope_sql()
            last = conn.execute(
                f"SELECT flow_id FROM data_flow WHERE {where} "
                "ORDER BY flow_id DESC LIMIT 1", params).fetchone()
            if last:
                num = int(re.search(r'\d+',
                                    scenarios.base_id(last['flow_id'])).group()) + 1
            else:
                num = 1
            fid = scenarios.scoped_id(self.scenario_id, f"PL-{num:03d}")
            now = datetime.datetime.now().isoformat()
            conn.execute("""INSERT INTO data_flow (flow_id,name,description,source_system_id,
                destination_system_id,direction,frequency,transfer_method,format,transformation_steps,
                business_rules,validations,error_handling,contains_pii,ferpa_protected,regulatory_driver,
                agreement_id,owner_id,status,known_issues,
                frequency_detail,organization,managed_by,data_steward_id,
                data_classification,encryption_method,disclosure_avoidance,
                data_quality_measures,data_standard,retention_at_destination,legal_authority,
                created_date,updated_date,scenario_id,change_state,display_name,
                manual_steps)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, data.get('name',''), data.get('description',''),
                 data.get('source_system_id',''), data.get('destination_system_id',''),
                 data.get('direction','Inbound'), data.get('frequency','Daily'),
                 data.get('transfer_method',''), data.get('format',''),
                 data.get('transformation_steps',''), data.get('business_rules',''),
                 data.get('validations',''), data.get('error_handling',''),
                 data.get('contains_pii',0), data.get('ferpa_protected',0),
                 data.get('regulatory_driver',''), fk(data.get('agreement_id')),
                 fk(data.get('owner_id')), data.get('status','Active'),
                 data.get('known_issues',''),
                 data.get('frequency_detail'), data.get('organization'),
                 data.get('managed_by'), fk(data.get('data_steward_id')),
                 data.get('data_classification'), data.get('encryption_method'),
                 data.get('disclosure_avoidance'),
                 data.get('data_quality_measures'), data.get('data_standard'),
                 data.get('retention_at_destination'), data.get('legal_authority'),
                 now, now, self.scenario_id,
                 change_state_for_new(self, data),
                 (data.get('display_name') or '').strip()[:MAX_DISPLAY_NAME] or None,
                 1 if data.get('manual_steps') else 0))
            # Attaching a flow to a system that had been tidied away brings it
            # back; otherwise the new flow would be invisible the moment it
            # was drawn.
            revealed = scenarios.reveal_for_flow(
                conn, self.scenario_id, data.get('source_system_id'),
                data.get('destination_system_id')) if self.scenario_id else []
            self.stamp_author(conn, "data_flow", "flow_id", fid, creating=True)
            row = conn.execute("SELECT * FROM data_flow WHERE flow_id=?", (fid,)).fetchone()
            self.audit_create(conn, "data_flow", fid, dict(row))
            conn.commit()
            conn.close()
            self.set_status(201)
            out = {"flow_id": fid}
            if revealed:
                # Told to the user, not done silently: they hid these, so they
                # should know why they are back.
                out["revealed"] = revealed
            self.write_json(out)

    class FlowHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

        def get(self, fid):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            where, params = self.scope_sql("f")
            row = conn.execute(f"""SELECT f.*, s1.name as source_name, s2.name as dest_name
                FROM data_flow f
                JOIN system s1 ON f.source_system_id=s1.system_id
                JOIN system s2 ON f.destination_system_id=s2.system_id
                WHERE f.flow_id=? AND {where}""", (fid,) + params).fetchone()
            fe_where, fe_params = self.scope_sql("fe")
            elements = conn.execute(f"""SELECT e.*, fe.is_transformed, fe.transformation_note
                FROM flow_element fe JOIN data_element e ON fe.element_id=e.element_id
                WHERE fe.flow_id=? AND {fe_where} ORDER BY e.element_id""",
                (fid,) + fe_params).fetchall()
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
                       'display_name',
                       'frequency_detail','organization','managed_by','data_steward_id',
                       'manual_steps',
                       'data_classification','encryption_method','disclosure_avoidance',
                       'data_quality_measures','data_standard','retention_at_destination','legal_authority')

        def put(self, fid):
            data = self.get_json_body()
            conn = get_db()
            now = datetime.datetime.now().isoformat()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            complaint = reject_proposed_on_live(self, data)
            if complaint:
                conn.close()
                return self.fail(400, complaint)
            where, params = self.scope_sql()
            before = conn.execute(
                f"SELECT * FROM data_flow WHERE flow_id=? AND {where}",
                (fid,) + params).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            gaps = missing_required("data_flow", data, before)
            if gaps:
                conn.close()
                return self.fail(400, f"These fields are required: {', '.join(gaps)}.",
                                 missing=gaps)
            fields = []
            vals = []
            for key in self.FLOW_FIELDS:
                if key in data:
                    value = data[key]
                    if key in FK_FIELDS:
                        value = fk(value)
                    elif key == "display_name":
                        # Capped here, not only in the browser: the API is the
                        # boundary, and an over-long label would spill across
                        # the diagram.
                        value = (value or "").strip()[:MAX_DISPLAY_NAME] or None
                    fields.append(f"{key}=?")
                    vals.append(value)
            if fields:
                fields.append("updated_date=?")
                fields.append("updated_by=?")
                vals.append(now)
                vals.append(self.current_user["user_id"])
                vals.append(fid)
                conn.execute(f"UPDATE data_flow SET {','.join(fields)} WHERE flow_id=?", vals)
            if "proposed" in data and self.scenario_id is not None:
                # Never overwrite "removed" from here. The Proposed checkbox
                # wrote change_state unconditionally, so saving a retired
                # record with the box clear quietly un-retired it, and with
                # the box checked claimed a live system was invented here.
                # Retiring has its own action; this only moves between
                # proposed and unchanged.
                conn.execute(
                    "UPDATE data_flow SET change_state=? "
                    "WHERE flow_id=? AND scenario_id=? AND change_state!=?",
                    (scenarios.PROPOSED if data["proposed"] else scenarios.UNCHANGED,
                     fid, self.scenario_id, scenarios.REMOVED))
            elif fields:
                mark_modified(conn, "data_flow", "flow_id", fid, self.scenario_id)
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

        def delete(self, fid):
            conn = get_db()
            try:
                try:
                    self.scenario_access(conn, write=True)
                except scenarios.ScenarioError as e:
                    return self.fail(e.status, e.message)
                where, params = self.scope_sql()
                before = conn.execute(
                    f"SELECT * FROM data_flow WHERE flow_id=? AND {where}",
                    (fid,) + params).fetchone()
                if not before:
                    return self.fail(404, "Not found")

                # In a scenario, retiring an existing flow is a proposal, not a
                # deletion: it stays on the diagram struck through.
                mode = self.get_argument("mode", "")
                if self.scenario_id is not None and mode in ("remove", "restore"):
                    try:
                        state = scenarios.set_retired(
                            conn, "data_flow", "flow_id", fid, self.scenario_id,
                            retired=(mode == "remove"))
                    except scenarios.RetireRefused as e:
                        return self.fail(400, str(e))
                    self.audit_update(
                        conn, "data_flow", fid, dict(before),
                        {**dict(before), "change_state": state},
                        fields=["change_state"])
                    conn.commit()
                    return self.write_json({"flow_id": fid, "change_state": state})
                # Its links to data elements, its attached resources and its
                # tasks go with it; nothing else references a flow.
                conn.execute("DELETE FROM flow_element WHERE flow_id=?", (fid,))
                conn.execute("DELETE FROM resource_version WHERE resource_id IN "
                             "(SELECT resource_id FROM resource "
                             " WHERE entity_type='data_flow' AND entity_id=?)", (fid,))
                conn.execute("DELETE FROM resource WHERE entity_type='data_flow' "
                             "AND entity_id=?", (fid,))
                delete_tasks_for(conn, "data_flow", fid)
                conn.execute("DELETE FROM data_flow WHERE flow_id=?", (fid,))
                self.audit_delete(conn, "data_flow", fid, dict(before))
                conn.commit()
            finally:
                conn.close()
            self.write_json({"deleted": fid})

    # -- Data Elements --
    class ElementsHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

        def get(self):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            # Copy-on-write: a scenario sees live elements until it forks one.
            where, params = self.scope_sql(table="data_element")
            rows = conn.execute(
                f"SELECT * FROM data_element WHERE {where} ORDER BY element_id",
                params).fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

    class ElementHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

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
            try:
                self.scenario_access(conn, write=True)
                # Copy-on-write: editing a shared element from inside a scenario
                # gives that scenario its own copy rather than changing live.
                eid = scenarios.fork_for_write(
                    conn, "data_element", "element_id", eid, self.scenario_id)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            before = conn.execute("SELECT * FROM data_element WHERE element_id=?",
                                  (eid,)).fetchone()
            if not before:
                conn.close()
                return self.fail(404, "Not found")
            before = dict(before)
            gaps = missing_required("data_element", data, before)
            if gaps:
                conn.close()
                return self.fail(400, f"These fields are required: {', '.join(gaps)}.",
                                 missing=gaps)
            fields = []
            vals = []
            for key in self.ELEMENT_FIELDS:
                if key in data:
                    fields.append(f"{key}=?")
                    vals.append(fk(data[key]) if key in FK_FIELDS else data[key])
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
    MAX_DESCRIPTION = 500

    CONTRIBUTOR_FIELDS = (
        'name', 'title', 'organization_unit', 'email', 'phone', 'role_type',
        'notes', 'governance_role', 'governance_responsibilities',
        'training_status', 'last_training_date', 'is_active',
    )

    # Deactivating someone removes them from everybody's pickers and hides them
    # from everybody's lists, so it is not an ordinary edit. An Editor may
    # correct a phone number; only a SuperAdmin may take a contributor out of
    # circulation or bring them back.
    CONTRIBUTOR_SUPERADMIN_FIELDS = {'is_active'}

    class ContributorsHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

        def get(self):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            where, params = self.scope_sql(table="contributor")
            # A deactivated contributor is still referenced by records and by
            # the audit trail, so the row is never removed — it is only taken
            # out of circulation. A SuperAdmin keeps seeing them, grayed, which
            # is the only way to find one again and reactivate it.
            if not permissions.at_least(self.current_user["role"],
                                        permissions.SUPERADMIN):
                where += " AND COALESCE(is_active,1)=1"
            rows = conn.execute(
                f"SELECT * FROM contributor WHERE {where} ORDER BY contributor_id",
                params).fetchall()
            payload = with_contact(rows, conn)
            conn.close()
            self.write_json(payload)

        def post(self):
            data = self.get_json_body()
            if not (data.get('name') or '').strip():
                return self.fail(400, "Name is required")
            # Creating a contributor from an existing account. Linking happens
            # in the same transaction as the insert, because a contributor
            # created "from" an account and then not attached to it is the
            # worst of both: a duplicate record that looks linked.
            link_user_id = data.get("link_user_id")
            conn = get_db()
            try:
                linked_row = None
                if link_user_id:
                    linked_row = conn.execute(
                        "SELECT user_id, email, contributor_id, is_active "
                        "FROM app_user WHERE user_id=?", (link_user_id,)).fetchone()
                    if not linked_row:
                        return self.fail(400, "That user account does not exist.")
                    if not linked_row["is_active"]:
                        return self.fail(
                            400, "That user account is deactivated.")
                    if linked_row["contributor_id"]:
                        # Checked here as well as grayed out in the list: the
                        # list is a snapshot, and two people could be creating
                        # contributors at once.
                        existing = conn.execute(
                            "SELECT name FROM contributor WHERE contributor_id=?",
                            (linked_row["contributor_id"],)).fetchone()
                        who = existing["name"] if existing else linked_row["contributor_id"]
                        return self.fail(
                            409, f"{linked_row['email']} is already linked to "
                                 f"{who}.")
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

                if linked_row:
                    conn.execute(
                        "UPDATE app_user SET contributor_id=?, updated_date=?, "
                        "updated_by=? WHERE user_id=?",
                        (cid, now, self.current_user["user_id"],
                         linked_row["user_id"]))
                    # Audited against the *user*, not the contributor: the
                    # contributor's own creation event already exists, and
                    # someone asking "when did this account get attached to a
                    # governance record" will look at the account.
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE,
                        entity_type="user", entity_id=linked_row["user_id"],
                        changes={"contributor_id": (None, cid)},
                        summary=f"Linked {linked_row['email']} to new "
                                f"contributor {values['name']} ({cid})",
                        ip_address=self.client_ip(), user_agent=self.user_agent())

                conn.commit()
                row = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                                   (cid,)).fetchone()
                payload = with_contact([row_to_dict(row)], conn)[0]
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(payload)

    TASK_FIELDS = ('name', 'task_type', 'task_type_other', 'description',
                   'anchor_kind', 'anchor_id', 'is_automated',
                   'pos_main', 'pos_cross', 'expected_start', 'expected_end',
                   'status', 'completed_date', 'notes')

    def next_task_id(conn, scenario_id, where, params):
        last = conn.execute(
            f"SELECT task_id FROM task WHERE {where} "
            f"ORDER BY task_id DESC LIMIT 1", params).fetchone()
        num = (int(re.search(r'\d+', scenarios.base_id(last['task_id'])).group()) + 1
               if last else 1)
        return scenarios.scoped_id(scenario_id, f"TSK-{num:03d}")

    def check_task_type(conn, ttype, other):
        """None if the type is usable, otherwise the complaint to return.

        One function because three handlers ask the same question, and a type
        accepted on create but refused on edit would be the sort of asymmetry
        that only shows up when somebody tries to fix a typo.
        """
        ttype = (ttype or "").strip()
        if not ttype:
            return "A task type is required."
        if ttype == TASK_TYPE_OTHER:
            if not (other or "").strip():
                return "Say what the task type is."
            return None
        if ttype not in {t["type_name"] for t in get_task_types(conn)}:
            return f"Unknown task type '{ttype}'."
        return None

    def check_task_dates(start, end, what="task"):
        """Complain if an end date precedes its start date, else None.

        One function because a task and each of its subtasks ask the same
        question, and a rule enforced on create but not on edit is the sort of
        asymmetry that only surfaces when somebody tries to fix a typo — the
        same reasoning as `check_task_type`.

        Dates are stored as 'YYYY-MM-DD' strings, so the comparison is a string
        comparison; there is nothing to parse and no timezone to get wrong.
        Equal dates are fine: a task that starts and finishes on one day is
        ordinary.

        Both the browser and this agree, and both are needed. The date inputs
        carry `min`/`max` so the picker will not offer an impossible date, but
        that is a convenience — it can be typed past, it does nothing about a
        row that predates this rule, and it is not a boundary. This is.
        """
        if start and end and end < start:
            return (f"A {what}'s expected end date cannot be before its "
                    f"expected start date ({start} to {end}).")
        return None

    def check_task_steps_dates(data):
        """The same check over every subtask in a payload, named by number.

        Subtasks arrive as a list and are written wholesale, so one bad pair
        has to be caught before anything is deleted — otherwise a refused save
        would already have removed the old rows.
        """
        if "steps" not in data:
            return None
        for i, step in enumerate(data.get("steps") or []):
            if not isinstance(step, dict):
                continue
            complaint = check_task_dates(step.get("expected_start"),
                                         step.get("expected_end"),
                                         what=f"subtask {i + 1}")
            if complaint:
                return complaint
        return None

    def check_task_dependencies(conn, handler, task_id, wanted):
        """Complain about a dependency list, or return the cleaned one.

        Three things can be wrong, and all three have to be refused rather
        than quietly dropped — a dependency that vanishes on save is the
        silently-unsaveable-field shape this project keeps finding.

        **A task cannot wait on itself.** Trivially true and trivially easy to
        click, since the picker is a list of tasks.

        **A dependency must be in the same view.** The same rule as a task's
        parent (`check_task_anchor`): a scenario task waiting on a live task is
        the cross-view reference the leak tests exist for, and the color
        coding would be reading a status from a world the viewer is not
        looking at.

        **A dependency must not close a loop.** A waits on B waits on A is not
        a plan, and the color rules would have each of them blocked by the
        other forever. The walk is over the graph as it would be *after* this
        save, which is why the proposed list is passed in rather than read
        back from the table.
        """
        where, params = handler.scope_sql()
        clean, seen = [], set()
        for dep in (wanted or []):
            dep = (dep or "").strip()
            if not dep or dep in seen:
                continue
            seen.add(dep)
            if dep == task_id:
                return None, "A task cannot depend on itself."
            row = conn.execute(
                f"SELECT task_id, name FROM task WHERE task_id=? AND {where}",
                (dep,) + params).fetchone()
            if not row:
                return None, (f"{dep} is not a task in this view, so it cannot "
                              f"be a dependency.")
            clean.append(dep)

        if clean:
            # Walk forward from each proposed dependency. If this task is
            # reachable, adding the link would close a loop.
            #
            # Breadth-first with a seen set rather than recursion: the data
            # already in the table is trusted to be acyclic (this check is the
            # only way in), but a seen set costs nothing and means a loop that
            # somehow got in cannot hang the request.
            edges = {}
            for r in conn.execute(
                    f"SELECT d.task_id, d.depends_on_task_id "
                    f"FROM task_dependency d JOIN task t ON t.task_id=d.task_id "
                    f"WHERE {where}", params).fetchall():
                edges.setdefault(r["task_id"], []).append(r["depends_on_task_id"])
            edges[task_id] = clean

            frontier, visited = list(clean), {task_id}
            while frontier:
                node = frontier.pop()
                if node == task_id:
                    return None, ("That would create a circular dependency: "
                                  f"{task_id} already comes before it.")
                if node in visited:
                    continue
                visited.add(node)
                frontier.extend(edges.get(node, []))
        return clean, None

    def check_task_anchor(conn, handler, kind, anchor_id):
        """Is this a real parent, visible in the view being written to?

        Both halves matter. An unknown kind or id is a client error; a parent
        in a *different* view is the cross-view reference the scenario leak
        tests exist to catch, and it would put a task on a diagram where its
        system does not exist.
        """
        if kind not in TASK_ANCHORS:
            return "A task must be attached to a system or a data flow."
        table, pk, _label = TASK_ANCHORS[kind]
        where, params = handler.scope_sql(table=table)
        found = conn.execute(
            f"SELECT 1 FROM {table} WHERE {pk}=? AND {where}",
            (anchor_id,) + params).fetchone()
        if not found:
            return ("That system or data flow is not part of the view you are "
                    "editing.")
        return None

    def write_task_children(conn, handler, task_id, data, replace_steps):
        """Save the people and, when asked, the steps.

        Steps are replaced wholesale rather than diffed. They are an ordered
        list a person edits as a list — reordering, inserting in the middle,
        deleting — and matching that up row by row produces either a diff
        algorithm or a pile of edge cases. Their statuses travel in the payload,
        so nothing is lost by rewriting them.
        """
        now = iso(utcnow())
        uid = handler.current_user["user_id"]

        if "contributor_ids" in data:
            conn.execute("DELETE FROM task_contributor WHERE task_id=?", (task_id,))
            seen = set()
            for cid in (data.get("contributor_ids") or []):
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                conn.execute(
                    """INSERT INTO task_contributor
                         (task_id, contributor_id, added_date, added_by)
                       VALUES (?,?,?,?)""", (task_id, cid, now, uid))

        # Replaced wholesale, like the people: the browser sends the list it
        # wants to end up with, and the pair being the primary key means a
        # duplicate is impossible rather than merely tidied up.
        if "dependency_ids" in data:
            conn.execute("DELETE FROM task_dependency WHERE task_id=?",
                         (task_id,))
            for dep in (data.get("dependency_ids") or []):
                conn.execute(
                    """INSERT INTO task_dependency
                         (task_id, depends_on_task_id, added_date, added_by)
                       VALUES (?,?,?,?)""", (task_id, dep, now, uid))

        if replace_steps and "steps" in data:
            conn.execute("DELETE FROM task_step WHERE task_id=?", (task_id,))
            for i, step in enumerate(data.get("steps") or []):
                text = (step.get("description") or "").strip()
                if not text:
                    continue
                status = step.get("status")
                if status not in TASK_STATUSES:
                    status = None
                conn.execute(
                    """INSERT INTO task_step
                         (task_id, sort_order, description, contributor_id,
                          status, expected_start, expected_end,
                          completed_date, notes, created_date,
                          updated_date, updated_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (task_id, (i + 1) * 10, text, fk(step.get("contributor_id")),
                     status,
                     step.get("expected_start") or None,
                     step.get("expected_end") or None,
                     step.get("completed_date") or None, step.get("notes"),
                     now, now, uid))

    class TasksHandler(BaseAPIHandler):
        """The task catalog, and creating one."""
        SCENARIO_AWARE = True

        def get(self):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            # Deliberately not `respect_hidden`: a task has no hidden flag of
            # its own. Scenario focus hides *systems and flows* to declutter
            # the diagram, and a task is drawn on its parent, so it disappears
            # with the parent and reappears with it. A separate flag would be a
            # second thing to keep in step, and a task hidden while its system
            # is showing would be a circle nobody could find.
            where, params = self.scope_sql()
            try:
                payload = task_rows(conn, where, params)
            finally:
                conn.close()
            self.write_json(payload)

        def post(self):
            data = self.get_json_body() or {}
            if not (data.get("name") or "").strip():
                return self.fail(400, "A task name is required.")
            kind = (data.get("anchor_kind") or "").strip()
            anchor_id = (data.get("anchor_id") or "").strip()

            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            try:
                complaint = reject_proposed_on_live(self, data)
                if complaint:
                    return self.fail(400, complaint)
                problem = check_task_anchor(conn, self, kind, anchor_id)
                if problem:
                    return self.fail(400, problem)

                complaint = check_task_dates(data.get("expected_start"),
                                             data.get("expected_end"))
                if complaint:
                    return self.fail(400, complaint)
                complaint = check_task_steps_dates(data)
                if complaint:
                    return self.fail(400, complaint)

                ttype = (data.get("task_type") or "").strip()
                other = (data.get("task_type_other") or "").strip()
                complaint = check_task_type(conn, ttype, other)
                if complaint:
                    return self.fail(400, complaint)
                # Only kept when it means something, so a type changed away
                # from Other does not leave its old wording behind.
                if ttype != TASK_TYPE_OTHER:
                    other = None

                where, params = self.scope_sql()
                tid = next_task_id(conn, self.scenario_id, where, params)
                now = iso(utcnow())
                uid = self.current_user["user_id"]
                conn.execute(
                    """INSERT INTO task
                         (task_id, name, task_type, task_type_other,
                          description, anchor_kind,
                          anchor_id, is_automated, pos_main, pos_cross,
                          expected_start,
                          expected_end, status, completed_date, notes,
                          created_date, updated_date, created_by, updated_by,
                          scenario_id, change_state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, data["name"].strip(), ttype, other,
                     data.get("description"),
                     kind, anchor_id,
                     # INTEGER 0/1 by convention, and coerced here so a JSON
                     # `true` from the browser cannot land as a string.
                     1 if data.get("is_automated") else 0,
                     data.get("pos_main"), data.get("pos_cross"),
                     data.get("expected_start") or None,
                     data.get("expected_end") or None,
                     # No default: a task nobody has assessed has no status,
                     # which is a different fact from "not started".
                     data.get("status") if data.get("status") in TASK_STATUSES
                     else None,
                     data.get("completed_date") or None, data.get("notes"),
                     now, now, uid, uid, self.scenario_id,
                     change_state_for_new(self, data)))
                if "dependency_ids" in data:
                    clean, complaint = check_task_dependencies(
                        conn, self, tid, data.get("dependency_ids"))
                    if complaint:
                        # Rolled back rather than left half-made: the task row
                        # is already inserted at this point.
                        conn.rollback()
                        return self.fail(400, complaint)
                    data["dependency_ids"] = clean
                write_task_children(conn, self, tid, data, replace_steps=True)
                apply_task_status(conn, tid)
                self.audit_create(conn, "task", tid,
                                  {k: data.get(k) for k in TASK_FIELDS})
                conn.commit()
                payload = task_rows(conn, "task_id=?", (tid,))[0]
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(payload)

    class TaskHandler(BaseAPIHandler):
        """One task: read it, change it, delete it."""
        SCENARIO_AWARE = True

        def _load(self, conn):
            where, params = self.scope_sql()
            return conn.execute(
                f"SELECT * FROM task WHERE task_id=? AND {where}",
                (self.path_args[0],) + params).fetchone()

        def get(self, tid):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            try:
                where, params = self.scope_sql()
                found = task_rows(conn, f"task_id=? AND {where}", (tid,) + params)
            finally:
                conn.close()
            if not found:
                return self.fail(404, "Not found")
            self.write_json(found[0])

        def put(self, tid):
            data = self.get_json_body() or {}
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            try:
                complaint = reject_proposed_on_live(self, data)
                if complaint:
                    return self.fail(400, complaint)
                before = self._load(conn)
                if not before:
                    return self.fail(404, "Not found")
                before = dict(before)
                # Merged with the stored row, because an update writes only the
                # keys it is sent: a payload carrying just one of the pair has
                # to be checked against the date already on the record, or
                # moving the end date alone would never be refused.
                complaint = check_task_dates(
                    data["expected_start"] if "expected_start" in data
                    else before.get("expected_start"),
                    data["expected_end"] if "expected_end" in data
                    else before.get("expected_end"))
                if complaint:
                    return self.fail(400, complaint)
                # Subtasks are replaced wholesale, so a bad pair has to be
                # caught before the DELETE in write_task_children runs.
                complaint = check_task_steps_dates(data)
                if complaint:
                    return self.fail(400, complaint)
                # Same reasoning for the dependencies, and the cycle walk has
                # to happen before anything is written or it would be checking
                # the graph it just changed.
                if "dependency_ids" in data:
                    clean, complaint = check_task_dependencies(
                        conn, self, tid, data.get("dependency_ids"))
                    if complaint:
                        return self.fail(400, complaint)
                    data["dependency_ids"] = clean
                gaps = missing_required("task", data, before)
                if gaps:
                    return self.fail(
                        400, f"These fields are required: {', '.join(gaps)}.",
                        missing=gaps)

                if "task_type" in data or "task_type_other" in data:
                    ttype = (data.get("task_type", before["task_type"]) or "").strip()
                    other = (data.get("task_type_other",
                                      before["task_type_other"]) or "").strip()
                    complaint = check_task_type(conn, ttype, other)
                    if complaint:
                        return self.fail(400, complaint)
                    # Changing away from Other clears the wording, so it cannot
                    # linger and reappear if someone switches back.
                    if ttype != TASK_TYPE_OTHER:
                        data["task_type_other"] = None
                # Moving a task to another parent is allowed — a validation step
                # genuinely does move when a flow is redrawn — but the new
                # parent is checked exactly as hard as the original was.
                if "anchor_kind" in data or "anchor_id" in data:
                    kind = (data.get("anchor_kind") or before["anchor_kind"]).strip()
                    aid = (data.get("anchor_id") or before["anchor_id"]).strip()
                    problem = check_task_anchor(conn, self, kind, aid)
                    if problem:
                        return self.fail(400, problem)
                    if (kind, aid) != (before["anchor_kind"], before["anchor_id"]):
                        # The stored position means different things on a system
                        # and on a flow (see migration 0016), so carrying it
                        # across would put the circle somewhere arbitrary.
                        # Clearing it lets the default placement run again.
                        data.setdefault("pos_main", None)
                        data.setdefault("pos_cross", None)

                fields, vals = [], []
                for key in TASK_FIELDS:
                    if key not in data:
                        continue
                    value = data[key]
                    if key == "status":
                        # An empty value clears it; anything unrecognized is
                        # ignored rather than stored.
                        if value in (None, "", "none"):
                            value = None
                        elif value not in TASK_STATUSES:
                            continue
                    if key == "name":
                        value = (value or "").strip()
                        if not value:
                            return self.fail(400, "A task name is required.")
                    if key in ("expected_start", "expected_end", "completed_date"):
                        value = value or None
                    if key == "is_automated":
                        # Boolean-ish columns are INTEGER 0/1 here, and the
                        # browser sends a JSON boolean. Coerced rather than
                        # stored as `true`, which would compare wrongly and
                        # read back as something the frontend does not expect.
                        value = 1 if value else 0
                    fields.append(f"{key}=?")
                    vals.append(value)
                now = iso(utcnow())
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [now, self.current_user["user_id"], tid]
                    conn.execute(
                        f"UPDATE task SET {','.join(fields)} WHERE task_id=?", vals)

                touched_children = "steps" in data or "contributor_ids" in data
                write_task_children(conn, self, tid, data, replace_steps=True)
                apply_task_status(conn, tid)

                if "proposed" in data and self.scenario_id is not None:
                    conn.execute(
                        "UPDATE task SET change_state=? "
                        "WHERE task_id=? AND scenario_id=? AND change_state!=?",
                        (scenarios.PROPOSED if data["proposed"]
                         else scenarios.UNCHANGED,
                         tid, self.scenario_id, scenarios.REMOVED))
                elif fields or touched_children:
                    mark_modified(conn, "task", "task_id", tid, self.scenario_id)

                after = dict(conn.execute(
                    "SELECT * FROM task WHERE task_id=?", (tid,)).fetchone())
                self.audit_update(conn, "task", tid, before, after,
                                  fields=TASK_FIELDS)
                conn.commit()
                payload = task_rows(conn, "task_id=?", (tid,))[0]
            finally:
                conn.close()
            self.write_json(payload)

        def delete(self, tid):
            conn = get_db()
            try:
                try:
                    self.scenario_access(conn, write=True)
                except scenarios.ScenarioError as e:
                    return self.fail(e.status, e.message)
                before = self._load(conn)
                if not before:
                    return self.fail(404, "Not found")

                # In a scenario, retiring is a proposal rather than a deletion,
                # matching systems and flows: the circle stays on the diagram
                # struck through so the plan reads as a change set.
                mode = self.get_argument("mode", "")
                if self.scenario_id is not None and mode in ("remove", "restore"):
                    try:
                        state = scenarios.set_retired(
                            conn, "task", "task_id", tid, self.scenario_id,
                            retired=(mode == "remove"))
                    except scenarios.RetireRefused as e:
                        return self.fail(400, str(e))
                    self.audit_update(
                        conn, "task", tid, dict(before),
                        {**dict(before), "change_state": state},
                        fields=["change_state"])
                    conn.commit()
                    return self.write_json({"task_id": tid, "change_state": state})

                # Steps and assignments are parts of the task, not records in
                # their own right, so they go with it.
                conn.execute("DELETE FROM task_step WHERE task_id=?", (tid,))
                conn.execute("DELETE FROM task_contributor WHERE task_id=?", (tid,))
                conn.execute(
                    "DELETE FROM task_dependency "
                    "WHERE task_id=? OR depends_on_task_id=?", (tid, tid))
                conn.execute("DELETE FROM resource_version WHERE resource_id IN "
                             "(SELECT resource_id FROM resource "
                             " WHERE entity_type='task' AND entity_id=?)", (tid,))
                conn.execute("DELETE FROM resource WHERE entity_type='task' "
                             "AND entity_id=?", (tid,))
                conn.execute("DELETE FROM task WHERE task_id=?", (tid,))
                self.audit_delete(conn, "task", tid, dict(before))
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True, "deleted": tid})

    class TaskCopyHandler(BaseAPIHandler):
        """Copy a task, carrying everything except the dates and the progress.

        This is what replaced recurrence. A quarterly job is four tasks, so
        making the second one has to be cheap — type, parent, position,
        description, notes, the people and the whole checklist come across.

        **Dependencies do not come across either**, and that is the same
        judgment as the dates. A copy is the next occurrence of a recurring
        job, and the task its predecessor waited on was the *previous*
        occurrence of something upstream — so a carried link would leave March's
        copy waiting on February's load, satisfied before it was made, which is
        worse than no link because it reads as a checked assumption. The new
        occurrence names what it waits on.

        **Dates and progress deliberately do not.** The dates are the one thing
        that differs between occurrences, and a copy that arrived pre-filled
        with March's dates would be wrong in a way nobody would notice until it
        was overdue. Progress resets for the same reason: a new occurrence has
        not been done, and inheriting checks from the last one would report work
        that never happened.
        """
        SCENARIO_AWARE = True

        def post(self, tid):
            data = self.get_json_body() or {}
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            try:
                where, params = self.scope_sql()
                src = conn.execute(
                    f"SELECT * FROM task WHERE task_id=? AND {where}",
                    (tid,) + params).fetchone()
                if not src:
                    return self.fail(404, "Not found")
                src = dict(src)

                new_id = next_task_id(conn, self.scenario_id, where, params)
                now = iso(utcnow())
                uid = self.current_user["user_id"]
                name = (data.get("name") or f"{src['name']} (copy)").strip()
                conn.execute(
                    """INSERT INTO task
                         (task_id, name, task_type, task_type_other,
                          description, anchor_kind,
                          anchor_id, is_automated, pos_main, pos_cross,
                          expected_start,
                          expected_end, status, completed_date, notes,
                          created_date, updated_date, created_by, updated_by,
                          scenario_id, change_state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id, name, src["task_type"], src["task_type_other"],
                     src["description"],
                     src["anchor_kind"], src["anchor_id"],
                     # Carried: whether a person has to do the work is a fact
                     # about the work, not about this occurrence of it. A
                     # nightly load copied for next quarter is still automated.
                     src["is_automated"],
                     # Position is dropped: two circles at identical
                     # coordinates look like one, and the copy would be
                     # undraggable because the original is on top of it.
                     None, None,
                     data.get("expected_start") or None,
                     data.get("expected_end") or None,
                     None, None, src["notes"],
                     now, now, uid, uid, self.scenario_id,
                     change_state_for_new(self, data)))

                for p in conn.execute(
                        "SELECT contributor_id FROM task_contributor WHERE task_id=?",
                        (tid,)).fetchall():
                    conn.execute(
                        """INSERT INTO task_contributor
                             (task_id, contributor_id, added_date, added_by)
                           VALUES (?,?,?,?)""",
                        (new_id, p["contributor_id"], now, uid))
                for st in conn.execute(
                        "SELECT * FROM task_step WHERE task_id=? "
                        "ORDER BY sort_order, step_id", (tid,)).fetchall():
                    # A subtask's own dates are dropped for the same reason
                    # the task's are: they are what differs between
                    # occurrences, and inheriting March's would be wrong in a
                    # way nobody notices until it is overdue.
                    conn.execute(
                        """INSERT INTO task_step
                             (task_id, sort_order, description, contributor_id,
                              status, expected_start, expected_end,
                              completed_date, notes, created_date,
                              updated_date, updated_by)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (new_id, st["sort_order"], st["description"],
                         st["contributor_id"], None, None, None,
                         None, st["notes"], now, now, uid))
                apply_task_status(conn, new_id)

                after = dict(conn.execute(
                    "SELECT * FROM task WHERE task_id=?", (new_id,)).fetchone())
                self.audit_create(conn, "task", new_id, after)
                audit.record_event(
                    conn, self.current_user, audit.CREATE, "task", new_id,
                    summary=f"Task copied from {tid}",
                    scenario_id=self.scenario_id,
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                payload = task_rows(conn, "task_id=?", (new_id,))[0]
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(payload)

    # -----------------------------------------------------------------------
    # Task types (everyone reads; SuperAdmin edits) — the same shape as system
    # types, for the same reason: the list belongs to whoever uses the app.
    # -----------------------------------------------------------------------
    class TaskTypesHandler(BaseAPIHandler):
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.SUPERADMIN

        def get(self):
            conn = get_db()
            try:
                types = get_task_types(conn)
                # Counted across every view, not just this one: a type in use
                # by a scenario is still in use, and reporting zero would offer
                # a delete that then has to be refused.
                counts = {r["task_type"]: r["n"] for r in conn.execute(
                    "SELECT task_type, COUNT(*) AS n FROM task "
                    "GROUP BY task_type").fetchall()}
            finally:
                conn.close()
            for t in types:
                t["in_use"] = counts.get(t["type_name"], 0)
            self.write_json({"types": types, "other": TASK_TYPE_OTHER,
                             "statuses": [
                {"value": v, "label": TASK_STATUS_LABELS[v]} for v in TASK_STATUSES]})

        def post(self):
            data = self.get_json_body() or {}
            name = (data.get("type_name") or "").strip()
            if not name:
                return self.fail(400, "A type name is required.")
            conn = get_db()
            try:
                if name == TASK_TYPE_OTHER:
                    return self.fail(
                        409, f"'{TASK_TYPE_OTHER}' is always offered, with a box "
                             f"to say what the task actually is, so it does not "
                             f"need to be in this list.")
                if conn.execute("SELECT 1 FROM task_type WHERE type_name=?",
                                (name,)).fetchone():
                    return self.fail(409, f"A type called '{name}' already exists.")
                top = conn.execute(
                    "SELECT COALESCE(MAX(sort_order),0) AS m FROM task_type").fetchone()
                now = iso(utcnow())
                conn.execute(
                    """INSERT INTO task_type
                         (type_name, description, sort_order, created_date,
                          updated_date, updated_by)
                       VALUES (?,?,?,?,?,?)""",
                    (name, data.get("description"), int(top["m"]) + 10, now, now,
                     self.current_user["user_id"]))
                self.audit_create(conn, "task_type", name,
                                  {"type_name": name,
                                   "description": data.get("description")})
                conn.commit()
                types = get_task_types(conn)
            finally:
                conn.close()
            self.set_status(201)
            self.write_json({"types": types})

    class TaskTypeHandler(BaseAPIHandler):
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def put(self, type_name):
            data = self.get_json_body() or {}
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM task_type WHERE type_name=?",
                                      (type_name,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                before = dict(before)
                new_name = (data.get("type_name") or type_name).strip()

                fields, vals = [], []
                if "description" in data:
                    fields.append("description=?")
                    vals.append(data["description"])
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), self.current_user["user_id"], type_name]
                    conn.execute(
                        f"UPDATE task_type SET {','.join(fields)} WHERE type_name=?",
                        vals)

                renamed = 0
                if new_name != type_name:
                    if conn.execute("SELECT 1 FROM task_type WHERE type_name=?",
                                    (new_name,)).fetchone():
                        return self.fail(
                            409, f"A type called '{new_name}' already exists.")
                    # Tasks store the type by name, so a rename carries across
                    # or every task using it is orphaned.
                    renamed = conn.execute(
                        "SELECT COUNT(*) FROM task WHERE task_type=?",
                        (type_name,)).fetchone()[0]
                    conn.execute(
                        "UPDATE task_type SET type_name=? WHERE type_name=?",
                        (new_name, type_name))
                    conn.execute("UPDATE task SET task_type=? WHERE task_type=?",
                                 (new_name, type_name))

                after = dict(conn.execute(
                    "SELECT * FROM task_type WHERE type_name=?",
                    (new_name,)).fetchone())
                self.audit_update(conn, "task_type", new_name, before, after,
                                  fields=("type_name", "description"))
                if renamed:
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE, "task", None,
                        summary=(f"Task type renamed '{type_name}' -> "
                                 f"'{new_name}'; {renamed} task(s) updated"),
                        ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                types = get_task_types(conn)
            finally:
                conn.close()
            self.write_json({"types": types, "renamed_tasks": renamed})

        def delete(self, type_name):
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM task_type WHERE type_name=?",
                                      (type_name,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                in_use = conn.execute("SELECT COUNT(*) FROM task WHERE task_type=?",
                                      (type_name,)).fetchone()[0]
                if in_use:
                    return self.fail(
                        409, f"{in_use} task(s) use the '{type_name}' type. "
                             f"Change them to another type first.")
                conn.execute("DELETE FROM task_type WHERE type_name=?", (type_name,))
                self.audit_delete(conn, "task_type", type_name, dict(before))
                conn.commit()
                types = get_task_types(conn)
            finally:
                conn.close()
            self.write_json({"types": types})

    class DeleteImpactHandler(BaseAPIHandler):
        """What deleting this record would destroy, before deciding to.

        Read-only and available to anyone who can delete the record, which is
        the same role that can edit it: the dialog is part of the delete, not
        a separate privilege.
        """
        READ_ROLE = permissions.EDITOR

        def get(self, kind, record_id):
            if kind not in ("systems", "flows"):
                return self.fail(404, "Not found")
            table, pk = (("system", "system_id") if kind == "systems"
                         else ("data_flow", "flow_id"))
            conn = get_db()
            try:
                row = conn.execute(
                    f"SELECT {pk} AS id, name FROM {table} WHERE {pk}=?",
                    (record_id,)).fetchone()
                if not row:
                    return self.fail(404, "Not found")
                payload = delete_impact(
                    conn, "system" if kind == "systems" else "flow", record_id)
                payload["record"] = {"id": row["id"], "name": row["name"]}
            finally:
                conn.close()
            self.write_json(payload)

    class ContributorReferencesHandler(BaseAPIHandler):
        """What names this contributor, before deactivating them.

        SuperAdmin, because only a SuperAdmin can act on the answer, and
        because it reports which user account is linked — which is more than
        the contributors screen otherwise shows.
        """
        READ_ROLE = permissions.SUPERADMIN

        def get(self, cid):
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT contributor_id, name, COALESCE(is_active,1) AS is_active "
                    "FROM contributor WHERE contributor_id=?", (cid,)).fetchone()
                if not row:
                    return self.fail(404, "Not found")
                payload = contributor_references(conn, cid)
                payload["contributor"] = {"contributor_id": row["contributor_id"],
                                          "name": row["name"],
                                          "is_active": row["is_active"]}
            finally:
                conn.close()
            self.write_json(payload)

    class ContributorHandler(BaseAPIHandler):
        SCENARIO_AWARE = True
        def get(self, cid):
            conn = get_db()
            row = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                               (cid,)).fetchone()
            if not row:
                conn.close()
                return self.fail(404, "Not found")
            payload = with_contact([row_to_dict(row)], conn)[0]
            conn.close()
            self.write_json(payload)

        def put(self, cid):
            data = self.get_json_body()
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
                cid = scenarios.fork_for_write(
                    conn, "contributor", "contributor_id", cid, self.scenario_id)
                before = conn.execute("SELECT * FROM contributor WHERE contributor_id=?",
                                      (cid,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                before = dict(before)
                gaps = missing_required("contributor", data, before)
                if gaps:
                    return self.fail(400, f"These fields are required: "
                                          f"{', '.join(gaps)}.", missing=gaps)
                is_super = permissions.at_least(self.current_user["role"],
                                                permissions.SUPERADMIN)
                fields, vals = [], []
                for key in CONTRIBUTOR_FIELDS:
                    if key not in data:
                        continue
                    if key in CONTRIBUTOR_SUPERADMIN_FIELDS and not is_super:
                        # Silently ignoring it would let the control appear to
                        # work; refusing says which field and why.
                        return self.fail(
                            403, "Only a SuperAdmin can deactivate or "
                                 "reactivate a contributor.")
                    fields.append(f"{key}=?")
                    if key == 'is_active':
                        vals.append(1 if data[key] else 0)
                    else:
                        vals.append(fk(data[key]) if key in FK_FIELDS else data[key])
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
                # The field diff records is_active 1 -> 0, which is accurate
                # and unreadable. Deactivating someone changes who appears in
                # every picker in the app, so it gets an event that says so in
                # words, with the scale of what still names them.
                was = 1 if before.get("is_active", 1) else 0
                now_active = 1 if after.get("is_active", 1) else 0
                if was != now_active:
                    refs = contributor_references(conn, cid)
                    verb = "Reactivated" if now_active else "Deactivated"
                    tail = (f"; still named on {refs['total']} record(s)"
                            if refs["total"] and not now_active else "")
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE,
                        entity_type="contributor", entity_id=cid,
                        summary=f"{verb} {after.get('name') or cid}{tail}",
                        scenario_id=self.scenario_id,
                        ip_address=self.client_ip(),
                        user_agent=self.user_agent())
                conn.commit()
                # The flyout is re-rendered straight from this response, so it
                # has to carry everything a GET would — contact_email included,
                # or editing a contributor would blank their address until the
                # panel was closed and reopened.
                after = with_contact([after], conn)[0]
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
        # Moving a node is an edit like any other, so it follows the same rules:
        # inside a scenario the scenario role decides, and the update is scoped
        # so a scenario's layout can never write over the live diagram's.
        SCENARIO_AWARE = True

        def put(self, sid):
            data = self.get_json_body()
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            where, params = self.scope_sql()
            conn.execute(
                f"UPDATE system SET x_pos=?, y_pos=? WHERE system_id=? AND {where}",
                (data.get('x_pos', 0), data.get('y_pos', 0), sid) + params)
            conn.commit()
            conn.close()
            self.write_json({"ok": True})

    class TaskPositionHandler(BaseAPIHandler):
        """Where a task's circle sits. Layout, not content.

        A separate endpoint from the task's own PUT for the same reason
        `FlowRouteHandler` is separate from a flow's: dragging a circle to tidy
        a diagram is **not audited** and does not mark a scenario record as
        Modified. Tidying a picture should not make a plan look like it changed.

        It also means a drag sends two numbers rather than the whole task, so
        a position cannot arrive carrying a stale copy of every other field.
        """
        SCENARIO_AWARE = True

        def put(self, tid):
            data = self.get_json_body() or {}
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            try:
                where, params = self.scope_sql()
                found = conn.execute(
                    f"SELECT 1 FROM task WHERE task_id=? AND {where}",
                    (tid,) + params).fetchone()
                if not found:
                    return self.fail(404, "Not found")
                # None is meaningful: it means "no stored position, use the
                # default slot", which is how a task moved to another parent
                # gets placed again.
                main = data.get("pos_main")
                cross = data.get("pos_cross")
                conn.execute(
                    "UPDATE task SET pos_main=?, pos_cross=? WHERE task_id=?",
                    (None if main is None else float(main),
                     None if cross is None else float(cross), tid))
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True, "task_id": tid,
                             "pos_main": data.get("pos_main"),
                             "pos_cross": data.get("pos_cross")})

    # -- Schema drill-down --
    class SystemSchemaHandler(BaseAPIHandler):
        """All tables + columns for a system's database schema (for the ER view)."""
        SCENARIO_AWARE = True

        def get(self, sid):
            conn = get_db()
            try:
                self.scenario_access(conn)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            where, params = self.scope_sql()
            system = conn.execute(
                f"SELECT * FROM system WHERE system_id=? AND {where}",
                (sid,) + params).fetchone()
            if not system:
                conn.close()
                self.set_status(404); self.write_json({"error": "Not found"}); return
            # A scenario copy reads the live system's schema until it has one of
            # its own, so the field list is there from the moment it is created.
            source_id, forked = scenarios.schema_source(conn, sid, self.scenario_id)
            tables = conn.execute(
                "SELECT * FROM schema_table WHERE system_id=? ORDER BY schema_name, table_name",
                (source_id,)).fetchall()
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
                # False means these rows still belong to Current Architecture and
                # are shown read-through; the first change forks them.
                "schema_is_local": forked,
            })

    class SchemaTablePositionHandler(BaseAPIHandler):
        SCENARIO_AWARE = True
        def put(self, table_id):
            data = self.get_json_body()
            conn = get_db()
            conn.execute("UPDATE schema_table SET x_pos=?, y_pos=? WHERE table_id=?",
                         (data.get("x_pos", 0), data.get("y_pos", 0), table_id))
            conn.commit()
            conn.close()
            self.write_json({"ok": True})

    def fork_schema_before_write(handler, conn, row_id, table, pk):
        """Redirect a schema edit onto the scenario's own copy.

        Inside a scenario the schema is shown read-through from Current
        Architecture until something changes it. Writing straight to the row
        that was read would edit the live schema — the exact leak the whole
        design exists to prevent. So the first edit forks the system's schema
        into the scenario, and the edit is applied to the copy instead.

        Returns the id to write to.
        """
        if handler.scenario_id is None:
            return row_id
        row = conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (row_id,)).fetchone()
        if row is None:
            return row_id
        if row["scenario_id"] == handler.scenario_id:
            # Already forked — by an earlier edit or by an upload. Still a
            # change to the system, so make sure it reads as Modified.
            owner = conn.execute(
                "SELECT system_id FROM schema_table WHERE table_id=?",
                (row_id if table == "schema_table" else row["table_id"],)).fetchone()
            if owner:
                mark_modified(conn, "system", "system_id", owner["system_id"],
                              handler.scenario_id)
            return row_id
        if row["scenario_id"] is not None:
            raise scenarios.ScenarioError("Not found.", 404)

        # Find the scenario's copy of the system this row belongs to.
        if table == "schema_table":
            live_system = row["system_id"]
        else:
            parent = conn.execute("SELECT system_id FROM schema_table WHERE table_id=?",
                                  (row["table_id"],)).fetchone()
            live_system = parent["system_id"] if parent else None
        copy = conn.execute(
            "SELECT system_id FROM system WHERE origin_id=? AND scenario_id=?",
            (live_system, handler.scenario_id)).fetchone()
        if not copy:
            raise scenarios.ScenarioError(
                "This schema belongs to a system that is not in this scenario.", 404)
        n = scenarios.fork_schema(conn, handler.scenario_id, copy["system_id"])
        if n:
            # One event for the whole fork, not one per column.
            audit.record_event(
                conn, handler.current_user, "update", "system", copy["system_id"],
                summary=f"Schema copied into the scenario before editing "
                        f"({n} field(s)); Current Architecture is unchanged",
                scenario_id=handler.scenario_id,
                ip_address=handler.client_ip(), user_agent=handler.user_agent())
        # Changing a system's schema is a change to that system, so the system
        # itself reads as Modified in the scenario. Without this the catalog
        # shows nothing changed while its field governance plainly has.
        mark_modified(conn, "system", "system_id", copy["system_id"],
                      handler.scenario_id)
        forked = conn.execute(
            f"SELECT {pk} FROM {table} WHERE origin_id=? AND scenario_id=?",
            (row_id, handler.scenario_id)).fetchone()
        return forked[pk] if forked else row_id

    class SchemaTableHandler(BaseAPIHandler):
        SCENARIO_AWARE = True

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
            try:
                self.scenario_access(conn, write=True)
                table_id = fork_schema_before_write(
                    self, conn, table_id, "schema_table", "table_id")
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
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
        SCENARIO_AWARE = True

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
            try:
                self.scenario_access(conn, write=True)
                column_id = fork_schema_before_write(
                    self, conn, column_id, "schema_column", "column_id")
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
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
        # Only the logo's arrangement and a cache token travel with the
        # settings; the image is fetched once from its own endpoint. The token
        # is what makes a replaced logo appear immediately.
        row = get_branding(conn)
        position = prefs.get("logo_position", "left")
        return {
            "app_title": prefs.get("app_title", "DATA LINEAGE TRACKER"),
            "app_subtitle": prefs.get("app_subtitle", ""),
            "logo_etag": row["etag"] if row else None,
            "logo_position": position if position in LOGO_POSITIONS else "left",
            "hide_app_title": str(prefs.get("hide_app_title", "false")).lower() == "true",
            "password_policy": auth.describe_password_policy(prefs),
            # Whether to draw "Forgot your password?" on the sign-in screen.
            # Resolved here rather than in the browser because it depends on
            # the mail configuration, which must not be sent to an anonymous
            # caller — the answer is one boolean, and the host, port and
            # username behind it are nobody's business before sign-in.
            "self_service_reset": reset_enabled(conn),
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
                    auth.verify_password(password, dummy_hash())  # equalize timing
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
    # Self-service password reset (0.9.0)
    # -----------------------------------------------------------------------

    def email_config(conn):
        return mailer.config_from_preferences(get_preferences(conn))

    def reset_enabled(conn):
        """Both switches, because either alone would be a trap.

        The preference is the organization's decision; a working transport is
        whether that decision can be honored. Offering the link with the
        preference on and no mail configured would send somebody to a screen
        that promises an email and delivers nothing, which is worse than not
        offering it — they would sit and wait rather than ask an administrator.
        """
        prefs = get_preferences(conn)
        on = str(prefs.get("enable_self_service_reset", "false")).lower() == "true"
        cfg = mailer.config_from_preferences(prefs)
        return on and mailer.is_configured(cfg) and bool(
            (prefs.get("app_base_url") or "").strip())

    RESET_EMAIL_SUBJECT = "Reset your Data Lineage Tracker password"

    def reset_email_body(first_name, link, minutes):
        who = f"Hello {first_name}," if first_name else "Hello,"
        return (
            f"{who}\n\n"
            f"Somebody asked to reset the password for this address. If that "
            f"was you, open the link below within {minutes} minutes:\n\n"
            f"{link}\n\n"
            f"The link can only be used once.\n\n"
            f"If it was not you, you can ignore this message \u2014 nothing has "
            f"changed and your current password still works.\n")

    class ForgotPasswordHandler(BaseAPIHandler):
        """Ask for a reset link.

        **The answer never says whether the address exists.** Same body, same
        status, same timing-insensitive path for an address with an account, an
        address without one, and a deactivated account. A forgot-password
        endpoint that distinguishes them is a free list of who works here,
        which for a state agency is a phishing target list.

        That is also why a send failure is not reported to the caller: "we
        could not send to that address" tells an attacker the address was real.
        It is logged for the administrator instead.
        """
        requires_auth = False

        def post(self):
            data = self.get_json_body() or {}
            email = auth.normalize_email(data.get("email"))
            conn = get_db()
            try:
                if not reset_enabled(conn):
                    # Not an error the browser can act on: the sign-in screen
                    # does not show the link unless this is on, so arriving
                    # here means a crafted request.
                    return self.fail(404, "Self-service password reset is not "
                                          "enabled on this deployment.")
                prefs = get_preferences(conn)
                row = conn.execute(
                    "SELECT * FROM app_user WHERE email=?", (email,)).fetchone()
                now = utcnow()
                if row and row["is_active"]:
                    window = iso(now - datetime.timedelta(
                        minutes=auth.RESET_RATE_WINDOW_MINUTES))
                    recent = conn.execute(
                        "SELECT COUNT(*) FROM password_reset_token "
                        "WHERE user_id=? AND created_date>=?",
                        (row["user_id"], window)).fetchone()[0]
                    if recent < auth.RESET_RATE_LIMIT:
                        raw, token_hash = auth.new_reset_token()
                        conn.execute(
                            """INSERT INTO password_reset_token
                                 (token_hash, user_id, created_date,
                                  expires_date, requested_ip)
                               VALUES (?,?,?,?,?)""",
                            (token_hash, row["user_id"], iso(now),
                             iso(now + datetime.timedelta(
                                 minutes=auth.RESET_TOKEN_MINUTES)),
                             self.client_ip()))
                        audit.record_event(
                            conn, None, audit.PASSWORD_RESET_REQUESTED,
                            "user", row["user_id"],
                            summary=f"Reset link requested for {row['email']}",
                            ip_address=self.client_ip(),
                            user_agent=self.user_agent())
                        conn.commit()
                        link = auth.reset_link(
                            (prefs.get("app_base_url") or "").strip(), raw)
                        ok, detail = mailer.send(
                            mailer.config_from_preferences(prefs),
                            row["email"], RESET_EMAIL_SUBJECT,
                            reset_email_body(row["first_name"], link,
                                             auth.RESET_TOKEN_MINUTES))
                        if not ok:
                            # The address is deliberately absent from this
                            # line: a log is not the place to pair a real
                            # address with a failure, and the administrator
                            # needs the reason rather than the recipient.
                            print(f"  reset email failed: {detail}",
                                  file=sys.stderr, flush=True)
            finally:
                conn.close()
            # One answer, always.
            self.write_json({"ok": True})

    class ResetPasswordHandler(BaseAPIHandler):
        """Spend a reset token and set a new password.

        Everything `ChangePasswordHandler` does on the way out — the policy
        check, revoking the other sessions, the audit entry — happens here too,
        because a reset is a password change that simply proved who it was a
        different way. The one thing it does **not** do is ask for the current
        password, which is the whole point.
        """
        requires_auth = False

        def post(self):
            data = self.get_json_body() or {}
            raw = (data.get("token") or "").strip()
            new = data.get("new_password") or ""
            # The same sentence for a token that never existed, one that has
            # expired and one already spent is tempting and wrong: a person who
            # clicked a link from an hour ago needs to know to ask for another,
            # and "expired" is not information an attacker can use — they
            # already hold the token in either case.
            conn = get_db()
            try:
                if not reset_enabled(conn):
                    return self.fail(404, "Self-service password reset is not "
                                          "enabled on this deployment.")
                token_hash = auth.hash_reset_token(raw)
                tok = conn.execute(
                    "SELECT * FROM password_reset_token WHERE token_hash=?",
                    (token_hash,)).fetchone()
                if not tok:
                    return self.fail(400, "This reset link is not valid. Ask "
                                          "for a new one.")
                if tok["used_date"]:
                    return self.fail(400, "This reset link has already been "
                                          "used. Ask for a new one.")
                if iso(utcnow()) > tok["expires_date"]:
                    return self.fail(400, "This reset link has expired. Ask "
                                          "for a new one.")
                row = conn.execute("SELECT * FROM app_user WHERE user_id=?",
                                   (tok["user_id"],)).fetchone()
                if not row or not row["is_active"]:
                    return self.fail(400, "This account is not active. Ask an "
                                          "administrator.")
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
                    (auth.hash_password(new), now, now, row["user_id"],
                     row["user_id"]))
                # Spent. Written before the commit so the row and the new
                # password land together: a crash between them would otherwise
                # leave a usable token for a password that had already changed.
                conn.execute(
                    "UPDATE password_reset_token SET used_date=? WHERE token_hash=?",
                    (now, token_hash))
                # Every other outstanding link for this account goes too. They
                # were all requested by whoever was locked out, and leaving a
                # second one live after the first was spent would mean a
                # forwarded email still worked.
                conn.execute(
                    "UPDATE password_reset_token SET used_date=? "
                    "WHERE user_id=? AND used_date IS NULL",
                    (now, row["user_id"]))
                # Every session, with no exception for "this one" — unlike a
                # change-password, nobody is signed in here, so an unrevoked
                # session would be somebody else's.
                conn.execute(
                    "UPDATE user_session SET revoked_at=? WHERE user_id=? "
                    "AND revoked_at IS NULL", (now, row["user_id"]))
                audit.record_event(
                    conn, None, audit.PASSWORD_RESET, "user", row["user_id"],
                    summary=f"Password reset by email link for {row['email']}",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True})

    class EmailTestHandler(BaseAPIHandler):
        """Send one message to the signed-in SuperAdmin, and report what happened.

        This exists because every other path deliberately hides its failures:
        a reset request must not say whether an address exists, so an
        administrator configuring mail has no way to find out that the port is
        wrong. Here the real reason is the whole point of the response, and it
        is safe because the recipient is the person asking.
        """
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def get(self):
            conn = get_db()
            try:
                self.write_json(mailer.describe(email_config(conn)))
            finally:
                conn.close()

        def post(self):
            conn = get_db()
            try:
                cfg = email_config(conn)
                prefs = get_preferences(conn)
            finally:
                conn.close()
            to = self.current_user["email"]
            base = (prefs.get("app_base_url") or "").strip()
            ok, detail = mailer.send(
                cfg, to, "Data Lineage Tracker test message",
                "This is a test message from the Data Lineage Tracker.\n\n"
                "If you are reading it, email delivery is working.\n"
                + (f"\nThe public address is set to {base}\n" if base else
                   "\nThe public address of this app is not set yet, so "
                   "password reset links cannot be built.\n"))
            self.write_json({"ok": ok, "detail": detail, "sent_to": to})

    # -----------------------------------------------------------------------
    # User administration
    # -----------------------------------------------------------------------
    USER_PUBLIC_FIELDS = (
        "user_id", "email", "first_name", "last_name", "title", "role",
        "is_active", "must_change_password", "contributor_id", "last_login_at",
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
                         (email, first_name, last_name, title, password_hash,
                          role, is_active, must_change_password, contributor_id,
                          created_date, updated_date, created_by, updated_by)
                       VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?)
                       RETURNING user_id""",
                    (email, first, last, (data.get("title") or "").strip() or None,
                     auth.hash_password(password), role,
                     1 if data.get("is_active", 1) else 0,
                     data.get("contributor_id") or None, now, now,
                     self.current_user["user_id"], self.current_user["user_id"])
                ).fetchone()
                uid = row["user_id"]
                latest = conn.execute(
                    "SELECT version FROM app_release WHERE is_published=1 "
                    "ORDER BY sort_key DESC LIMIT 1").fetchone()
                if latest:
                    conn.execute("UPDATE app_user SET last_seen_release=? WHERE user_id=?",
                                 (latest["version"], uid))
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
                for key in ("first_name", "last_name", "title", "contributor_id"):
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
                                  fields=("email", "first_name", "last_name",
                                          "title", "role", "is_active",
                                          "contributor_id",
                                          "must_change_password"))
                conn.commit()
            finally:
                conn.close()
            self.write_json(user_row_public(after))

    class PickableUsersHandler(BaseAPIHandler):
        """Accounts you can turn into a contributor, for the Add Contributor list.

        Not `/api/users`, which is Administrator-only and returns roles, status
        and password metadata. Creating a contributor is an Editor's job, so
        this exposes the minimum that job needs — who they are, and whether
        they are already attached to a contributor — and nothing about their
        access.

        Already-linked accounts are **returned, not filtered out**, with the
        contributor they belong to. Omitting them would leave someone hunting
        for a colleague who is simply not in the list; saying "already linked
        to Dana Rowe" answers the question on the spot.
        """
        READ_ROLE = permissions.EDITOR

        def get(self):
            conn = get_db()
            try:
                rows = conn.execute(
                    """SELECT u.user_id, u.first_name, u.last_name, u.email,
                              u.title, u.contributor_id,
                              c.name AS contributor_name
                         FROM app_user u
                         LEFT JOIN contributor c
                                ON c.contributor_id = u.contributor_id
                        WHERE u.is_active = 1
                        ORDER BY u.first_name, u.last_name""").fetchall()
            finally:
                conn.close()
            self.write_json([{
                "user_id": r["user_id"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(),
                "email": r["email"],
                "title": r["title"],
                "linked_contributor_id": r["contributor_id"],
                "linked_contributor_name": r["contributor_name"],
            } for r in rows])

    class ProfileHandler(BaseAPIHandler):
        """Your own name and title. Not your email, and not your role.

        A separate endpoint from `/api/users/<id>` rather than a relaxation of
        it. That one is an administrator's tool: it can change a role, issue a
        temporary password, deactivate an account, and it refuses when the
        actor cannot manage the target. Letting a Viewer through it with a
        narrower field list would mean the protections lived in a list of
        allowed keys rather than in the role check, and the next field added to
        that handler would be editable by everyone by default.

        **Email is deliberately not here.** It is the sign-in identity and the
        name against every audit entry, so changing it changes both how someone
        signs in and how their history reads. That stays an administrator
        action.

        Password changes go through `/api/auth/change-password`, which already
        verifies the current password, enforces the policy, revokes the other
        sessions and audits the change.
        """
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER

        PROFILE_FIELDS = ("first_name", "last_name", "title")

        def put(self):
            data = self.get_json_body() or {}
            uid = self.current_user["user_id"]

            # Refuse rather than ignore. Silently dropping `role` would let a
            # request that looks successful do nothing, which is the shape of
            # bug this project keeps finding; and saying so makes the boundary
            # discoverable rather than mysterious.
            forbidden = sorted(set(data) - set(self.PROFILE_FIELDS))
            if forbidden:
                return self.fail(
                    403, "Your profile covers your name and title. "
                         f"{', '.join(forbidden)} cannot be changed here.",
                    fields=forbidden)

            if "first_name" in data and not (data.get("first_name") or "").strip():
                return self.fail(400, "First name is required.")
            if "last_name" in data and not (data.get("last_name") or "").strip():
                return self.fail(400, "Last name is required.")

            conn = get_db()
            try:
                before = dict(conn.execute(
                    "SELECT * FROM app_user WHERE user_id=?", (uid,)).fetchone())
                fields, vals = [], []
                for key in self.PROFILE_FIELDS:
                    if key in data:
                        value = (data[key] or "").strip()
                        fields.append(f"{key}=?")
                        vals.append(value or None)
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), uid, uid]
                    conn.execute(
                        f"UPDATE app_user SET {','.join(fields)} WHERE user_id=?",
                        vals)
                after = dict(conn.execute(
                    "SELECT * FROM app_user WHERE user_id=?", (uid,)).fetchone())
                # Audited against themselves, so "who changed this" has an
                # answer even when the answer is the person it describes.
                self.audit_update(conn, "user", uid, before, after,
                                  fields=self.PROFILE_FIELDS)
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
                    # Preferences are otherwise free text, but this one drives a
                    # CSS class, so a junk value would reach the stylesheet.
                    # public_settings falls back to "left" as well; refusing it
                    # here keeps the bad value out of the database in the first
                    # place.
                    if key == "logo_position" and new_value not in LOGO_POSITIONS:
                        return self.fail(
                            400, "Logo position must be one of: "
                                 + ", ".join(LOGO_POSITIONS) + ".")
                    # Same reasoning as logo_position: these two choose a code
                    # path at send time, so a junk value would be discovered by
                    # a password reset failing rather than by a save failing.
                    if (key == "email_transport"
                            and new_value not in mailer.TRANSPORTS):
                        return self.fail(
                            400, "Email delivery must be one of: "
                                 + ", ".join(mailer.TRANSPORTS) + ".")
                    if (key == "smtp_security"
                            and new_value not in mailer.SMTP_SECURITY):
                        return self.fail(
                            400, "SMTP encryption must be one of: "
                                 + ", ".join(mailer.SMTP_SECURITY) + ".")
                    # A base URL that is not absolute produces a reset link
                    # nobody can click, and the failure shows up in somebody
                    # else's inbox rather than here.
                    if key == "app_base_url" and new_value.strip():
                        if not new_value.strip().lower().startswith(
                                ("http://", "https://")):
                            return self.fail(
                                400, "The public address must start with "
                                     "http:// or https://.")
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
        SCENARIO_AWARE = True
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
                                                   scenario_id=self.scenario_id,
                                                   **filters)
                # Fixed lists rather than DISTINCT scans over a growing table.
                actions = [audit.CREATE, audit.UPDATE, audit.DELETE, audit.LOGIN,
                           audit.LOGOUT, audit.LOGIN_FAILED, audit.PERMISSION_DENIED,
                           audit.PASSWORD_CHANGED, audit.BACKFILL]
                entities = ["system", "data_flow", "data_element", "contributor",
                            "schema_table", "schema_column", "system_schema",
                            "system_resource", "data_flow_resource", "user",
                            "session", "preference", "release"]
            finally:
                conn.close()
            for e in events:
                e["revertible"] = (e.get("action") == audit.UPDATE
                                   and e.get("entity_type") in REVERTIBLE_ENTITIES
                                   and bool(e.get("changes")))
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
                events, _ = audit.query_events(conn, limit=100000, offset=0,
                                               scenario_id=self.scenario_id, **filters)
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

    # -----------------------------------------------------------------------
    # Manual Entry helpers
    # -----------------------------------------------------------------------
    MANUAL_ENTRY = "Manual Entry"
    FIELD_LIST_LABEL = "Data entry field list"

    def next_flow_id(conn, scenario_id=None):
        r"""Next free flow identifier within one view.

        Scoped, and parsed from the un-prefixed id: in a scenario the highest
        id is something like ``S3-PL-007``, where a bare ``\d+`` search finds
        the *scenario* number and generates a colliding ``PL-002``.

        The docstring is raw because of that ``\d+``: in a normal string it is
        an invalid escape, which Python warns about today and will reject
        outright in a future version.
        """
        where, params = scenarios.scope(scenario_id)
        last = conn.execute(
            f"SELECT flow_id FROM data_flow WHERE {where} "
            "ORDER BY flow_id DESC LIMIT 1", params).fetchone()
        num = (int(re.search(r"\d+", scenarios.base_id(last["flow_id"])).group()) + 1
               if last else 1)
        return scenarios.scoped_id(scenario_id, f"PL-{num:03d}")

    def sync_manual_entry(handler, conn, system_id, data, creating=False):
        """Keep a Manual Entry system's inbound data flow in step with its target.

        The flow into the target data store is created with the system, so the
        person only answers the timing questions once. If the target is changed
        later the existing flow is repointed rather than orphaned.
        """
        sys_row = conn.execute("SELECT * FROM system WHERE system_id=?", (system_id,)).fetchone()
        if not sys_row or sys_row["system_type"] != MANUAL_ENTRY:
            return None
        target = sys_row["target_system_id"]
        if not target:
            return None
        if not conn.execute("SELECT 1 FROM system WHERE system_id=?", (target,)).fetchone():
            return None

        where, params = scenarios.scope(handler.scenario_id)
        existing = conn.execute(
            f"SELECT * FROM data_flow WHERE source_system_id=? AND {where} "
            "ORDER BY flow_id LIMIT 1", (system_id,) + params).fetchone()
        now = iso(utcnow())
        target_name = conn.execute("SELECT name FROM system WHERE system_id=?",
                                   (target,)).fetchone()["name"]
        flow_name = f"{sys_row['name']} to {target_name}"

        if existing:
            if existing["destination_system_id"] != target:
                before = dict(existing)
                conn.execute(
                    """UPDATE data_flow SET destination_system_id=?, name=?,
                           updated_date=?, updated_by=? WHERE flow_id=?""",
                    (target, flow_name, now, handler.current_user["user_id"],
                     existing["flow_id"]))
                after = dict(conn.execute("SELECT * FROM data_flow WHERE flow_id=?",
                                          (existing["flow_id"],)).fetchone())
                handler.audit_update(conn, "data_flow", existing["flow_id"], before, after,
                                     fields=("destination_system_id", "name"))
            return existing["flow_id"]

        fid = next_flow_id(conn, handler.scenario_id)
        conn.execute(
            """INSERT INTO data_flow
                 (flow_id, name, description, source_system_id, destination_system_id,
                  direction, frequency, frequency_detail, transfer_method, format,
                  contains_pii, ferpa_protected, organization, managed_by,
                  data_steward_id, owner_id, status, created_date, updated_date,
                  created_by, updated_by, scenario_id, change_state)
               VALUES (?,?,?,?,?,'Inbound',?,?,?,?,?,?,?,?,?,?, 'Active',?,?,?,?,?,?)""",
            (fid, flow_name,
             data.get("flow_description") or
             f"Data entered manually via {sys_row['name']} and stored in {target_name}.",
             system_id, target,
             data.get("flow_frequency") or "Ad Hoc",
             data.get("flow_frequency_detail"),
             data.get("flow_transfer_method") or
             ("File upload" if sys_row["entry_type"] == "File Upload" else "Manual entry"),
             data.get("flow_format") or
             ("File" if sys_row["entry_type"] == "File Upload" else "Keyed entry"),
             1 if data.get("flow_contains_pii") else 0,
             1 if data.get("flow_ferpa_protected") else 0,
             sys_row["organization"], sys_row["managed_by"], sys_row["data_steward_id"],
             sys_row["owner_id"], now, now,
             handler.current_user["user_id"], handler.current_user["user_id"],
             handler.scenario_id,
             # The flow belongs to the same plan as the system that needs it,
             # so it inherits that system's state rather than defaulting.
             sys_row["change_state"] if handler.scenario_id else scenarios.UNCHANGED))
        handler.audit_create(conn, "data_flow", fid,
                             {"name": flow_name, "source_system_id": system_id,
                              "destination_system_id": target,
                              "created_with": "Manual Entry system"})
        return fid

    def ensure_field_list_resource(handler, conn, entity_id, entity_type="system"):
        """Give a record a fixed slot for its field list.

        Created automatically for Manual Entry systems, and on request for a
        data flow. Tagged with the caller's scenario so a scenario's field list
        does not appear on Current Architecture.
        """
        exists = conn.execute(
            "SELECT 1 FROM resource WHERE entity_type=? AND entity_id=? AND label=?",
            (entity_type, entity_id, FIELD_LIST_LABEL)).fetchone()
        if exists:
            return
        now = iso(utcnow())
        conn.execute(
            """INSERT INTO resource
                 (entity_type, entity_id, label, kind, label_locked, sort_order,
                  created_date, updated_date, created_by, updated_by, scenario_id)
               VALUES (?,?,?,'file',1,0,?,?,?,?,?)""",
            (entity_type, entity_id, FIELD_LIST_LABEL, now, now,
             handler.current_user["user_id"], handler.current_user["user_id"],
             handler.scenario_id))

    # -----------------------------------------------------------------------
    # Version and release notes
    # -----------------------------------------------------------------------
    class VersionHandler(BaseAPIHandler):
        """Public: the sign-in screen shows the version before anyone is authenticated."""
        requires_auth = False

        def get(self):
            self.write_json({"version": VERSION})

    # -----------------------------------------------------------------------
    # Branding
    # -----------------------------------------------------------------------
    def get_branding(conn):
        """The single branding row, or None if no logo has been uploaded."""
        return conn.execute(
            "SELECT * FROM app_branding WHERE branding_id=1").fetchone()

    # -----------------------------------------------------------------------
    # Field definitions
    # -----------------------------------------------------------------------
    # A definition stored with this instead of a record type applies to every
    # record type that has the column. Eleven columns appear on more than one —
    # data_steward_id on three — and keeping those in step by hand means they
    # drift. Storing the shared text once is what makes one edit reach them all.
    SHARED = "*"

    def field_definitions(conn):
        """{(entity, column): definition}, including the shared rows."""
        return {(r["entity_type"], r["column_name"]): r["definition"]
                for r in conn.execute(
                    "SELECT entity_type, column_name, definition "
                    "FROM field_definition").fetchall()}

    def effective_definition(defs, entity, column):
        """What a field actually shows.

        A definition written for one record type wins over the shared one, so
        turning on sharing never destroys wording someone wrote deliberately —
        "Description" can mean something different on a system and on a data
        flow while "Data Steward" is defined once.
        """
        own = defs.get((entity, column))
        if own:
            return own, False
        shared = defs.get((SHARED, column))
        return (shared, True) if shared else (None, False)

    def shared_columns():
        """{column: [entity, ...]} for columns on more than one record type."""
        out = {}
        for f in field_catalog.FIELDS:
            out.setdefault(f["column"], []).append(f["entity"])
        return {c: e for c, e in out.items() if len(e) > 1}

    def definition_lookup(conn):
        """What the frontend needs: ``"entity|label" -> definition``.

        Resolved here rather than in the browser because the label a component
        is given is not a reliable key on its own — the read view, the edit
        view and the Add modal spell the same column differently. The catalog
        knows every spelling, so one definition is emitted under each of them
        and a component can look itself up knowing only its own label.
        """
        defs = field_definitions(conn)
        out = {}
        for f in field_catalog.FIELDS:
            text, _ = effective_definition(defs, f["entity"], f["column"])
            if not text:
                continue
            for key in ("label", "read_label", "modal_label"):
                if f.get(key):
                    out[f"{f['entity']}|{f[key]}"] = text
        return out

    def hidden_fields(conn):
        """{(entity, column)} for the fields this department has turned off."""
        return {(r["entity_type"], r["column_name"]) for r in conn.execute(
            "SELECT entity_type, column_name FROM field_hidden "
            "WHERE hidden=1").fetchall()}

    def required_columns():
        """{(entity, column)} that an Add window validates as required.

        Read from the generated catalog rather than listed here, because the
        authority is `useRequired` in the UI and a second hand-kept list would
        drift. The generator fails if the two disagree.
        """
        return {(f["entity"], f["column"]) for f in field_catalog.FIELDS
                if f.get("required")}

    def hidden_lookup(conn):
        """What the frontend needs: the hidden set, and labels to columns.

        Keyed on the **column**, unlike `definition_lookup`, which is keyed on
        every label a column is given. The difference is deliberate. A
        definition has to be findable from the label a component was handed,
        because there are 81 call sites that pass nothing else. Hiding is asked
        by the newer field components, which declare their column outright, so
        resolving on the column means a field relabeled in one view cannot
        quietly reappear there.

        The label-to-column map is emitted alongside so the older components —
        `EditField`, `ReadField` and the two person pickers, which still know
        only their label — can resolve themselves the same way.
        """
        hidden = hidden_fields(conn)
        columns = {}
        for f in field_catalog.FIELDS:
            for key in ("label", "read_label", "modal_label"):
                if f.get(key):
                    columns[f"{f['entity']}|{f[key]}"] = f["column"]
        # Required fields are filtered out here as well as refused on write.
        # A row could predate a field becoming required, and drawing nothing
        # where the form then demands a value is the one failure mode that
        # makes a record impossible to create.
        req = required_columns()
        return {
            "hidden": sorted(f"{e}|{c}" for e, c in hidden if (e, c) not in req),
            "columns": columns,
        }

    class FieldDefinitionsHandler(BaseAPIHandler):
        """Read the definitions for rendering; any signed-in user may.

        Definitions are explanatory text about the department's own data model,
        shown to everyone who can see the fields they describe, so there is
        nothing to restrict here. Writing them is SuperAdmin, below.
        """
        READ_ROLE = permissions.VIEWER
        allow_when_password_expired = True

        def get(self):
            conn = get_db()
            try:
                payload = {"definitions": definition_lookup(conn)}
                payload.update(hidden_lookup(conn))
            finally:
                conn.close()
            self.write_json(payload)

    class FieldCatalogHandler(BaseAPIHandler):
        """The whole catalog with its definitions, for the Preferences editor."""
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def get(self):
            conn = get_db()
            try:
                defs = field_definitions(conn)
            finally:
                conn.close()
            conn = get_db()
            try:
                hidden = hidden_fields(conn)
            finally:
                conn.close()
            multi = shared_columns()
            req = required_columns()
            fields = []
            for f in field_catalog.FIELDS:
                text, inherited = effective_definition(defs, f["entity"], f["column"])
                fields.append({
                    "entity": f["entity"],
                    "column": f["column"],
                    "label": f["label"],
                    "section": f["section"],
                    # Where the wording differs by surface, say so: someone
                    # writing a definition should know the field is called
                    # something else elsewhere, rather than wondering whether
                    # it is a different field.
                    "aliases": sorted({f[k] for k in ("read_label", "modal_label")
                                       if f.get(k) and f[k] != f["label"]}),
                    "definition": text or "",
                    # True when the text comes from the shared row rather than
                    # from this record type, so the editor can say so instead
                    # of looking as though it were written here.
                    "inherited": inherited,
                    "own_definition": defs.get((f["entity"], f["column"]), ""),
                    # The other record types carrying this same column.
                    "shared_with": [e for e in multi.get(f["column"], [])
                                    if e != f["entity"]],
                    # Hiding is per record type: no shared scope, so this is a
                    # plain flag rather than the two-level resolution a
                    # definition needs.
                    "hidden": (f["entity"], f["column"]) in hidden,
                    "required": (f["entity"], f["column"]) in req,
                })
            self.write_json({"fields": fields,
                             "shared": {c: defs.get((SHARED, c), "")
                                        for c in sorted(multi)},
                             "entity_labels": ENTITY_LABELS,
                             "max_length": MAX_FIELD_DEFINITION})

        def put(self):
            data = self.get_json_body() or {}
            entity = (data.get("entity") or "").strip()
            column = (data.get("column") or "").strip()
            text = (data.get("definition") or "").strip()

            # Only fields the catalog knows about. Without this the table
            # would accept definitions for anything, including typos that then
            # silently never appear anywhere.
            known = {(f["entity"], f["column"]) for f in field_catalog.FIELDS}
            if entity == SHARED:
                # A shared definition only means anything for a column that
                # more than one record type actually has.
                if column not in shared_columns():
                    return self.fail(
                        400, "Only a field that appears on more than one record "
                             "type can have one shared definition.")
            elif (entity, column) not in known:
                return self.fail(400, "That is not a field this application has.")
            if len(text) > MAX_FIELD_DEFINITION:
                return self.fail(400, f"A definition must be "
                                      f"{MAX_FIELD_DEFINITION} characters or fewer.")

            conn = get_db()
            try:
                before = conn.execute(
                    "SELECT definition FROM field_definition "
                    "WHERE entity_type=? AND column_name=?",
                    (entity, column)).fetchone()
                old = before["definition"] if before else ""
                now = iso(utcnow())
                if not text:
                    # Empty means "no definition", which is a deletion rather
                    # than an empty string: the tooltip should disappear, not
                    # show a blank bubble.
                    conn.execute(
                        "DELETE FROM field_definition "
                        "WHERE entity_type=? AND column_name=?", (entity, column))
                elif before:
                    conn.execute(
                        "UPDATE field_definition SET definition=?, updated_date=?, "
                        "updated_by=? WHERE entity_type=? AND column_name=?",
                        (text, now, self.current_user["user_id"], entity, column))
                else:
                    conn.execute(
                        """INSERT INTO field_definition
                             (entity_type, column_name, definition, updated_date,
                              updated_by)
                           VALUES (?,?,?,?,?)""",
                        (entity, column, text, now, self.current_user["user_id"]))

                if old != text:
                    if entity == SHARED:
                        ents = shared_columns().get(column, [])
                        where = ("shared across " +
                                 ", ".join(ENTITY_LABELS.get(e, e) for e in ents))
                        label = next((f["label"] for f in field_catalog.FIELDS
                                      if f["column"] == column), column)
                    else:
                        where = ENTITY_LABELS.get(entity, entity)
                        label = next((f["label"] for f in field_catalog.FIELDS
                                      if f["entity"] == entity
                                      and f["column"] == column), column)
                    verb = ("Removed the definition for" if not text
                            else "Defined" if not old else "Reworded the definition for")
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE,
                        entity_type="field_definition",
                        entity_id=f"{entity}.{column}",
                        changes={f"{entity}.{column}": (old, text)},
                        summary=f"{verb} {label} ({where})",
                        ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                defs = field_definitions(conn)
            finally:
                conn.close()
            self.write_json({"definition": defs.get((entity, column), "")})

    class FieldVisibilityHandler(BaseAPIHandler):
        """Turn a field off, or back on, for one record type.

        Separate from `FieldCatalogHandler.put` rather than another branch of
        it. The two write to different tables with different rules — a
        definition may be shared across record types and is refused above the
        length cap; hiding is per record type and is refused for a required
        field — and a single PUT that silently did one or the other depending
        on which keys happened to be present is the kind of endpoint that gets
        called wrongly later.
        """
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def put(self):
            data = self.get_json_body() or {}
            entity = (data.get("entity") or "").strip()
            column = (data.get("column") or "").strip()
            hide = bool(data.get("hidden"))

            # No shared scope here, unlike definitions. See migration 0012:
            # whether a field is *used* is a per-record-type question, and one
            # check emptying three screens is a worse surprise than three checks.
            if entity == SHARED:
                return self.fail(
                    400, "Hiding is set for one record type at a time. "
                         "A field that appears on several is hidden on each "
                         "separately.")
            known = {(f["entity"], f["column"]) for f in field_catalog.FIELDS}
            if (entity, column) not in known:
                return self.fail(400, "That is not a field this application has.")
            if (entity, column) in required_columns():
                # The Add window will not submit without it, so a hidden
                # required field makes that record type impossible to create.
                label = next((f["label"] for f in field_catalog.FIELDS
                              if f["entity"] == entity and f["column"] == column),
                             column)
                return self.fail(
                    400, f"\u201c{label}\u201d is required when creating a "
                         f"{ENTITY_LABELS.get(entity, entity)}, so it cannot "
                         f"be hidden.")

            conn = get_db()
            try:
                before = conn.execute(
                    "SELECT hidden FROM field_hidden "
                    "WHERE entity_type=? AND column_name=?",
                    (entity, column)).fetchone()
                was = bool(before and before["hidden"])
                now = iso(utcnow())
                if before:
                    conn.execute(
                        "UPDATE field_hidden SET hidden=?, updated_date=?, "
                        "updated_by=? WHERE entity_type=? AND column_name=?",
                        (1 if hide else 0, now, self.current_user["user_id"],
                         entity, column))
                else:
                    conn.execute(
                        """INSERT INTO field_hidden
                             (entity_type, column_name, hidden, updated_date,
                              updated_by)
                           VALUES (?,?,?,?,?)""",
                        (entity, column, 1 if hide else 0, now,
                         self.current_user["user_id"]))

                if was != hide:
                    label = next((f["label"] for f in field_catalog.FIELDS
                                  if f["entity"] == entity
                                  and f["column"] == column), column)
                    where = ENTITY_LABELS.get(entity, entity)
                    verb = "Hid" if hide else "Restored"
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE,
                        entity_type="field_hidden",
                        entity_id=f"{entity}.{column}",
                        changes={f"{entity}.{column}": ("hidden" if was else "shown",
                                                        "hidden" if hide else "shown")},
                        summary=f"{verb} {label} on {where}",
                        ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                hidden = hidden_fields(conn)
            finally:
                conn.close()
            self.write_json({"hidden": (entity, column) in hidden})

    class PublicBrandingHandler(BaseAPIHandler):
        """Branding for the sign-in screen, which by definition has no session.

        Without this the sign-in screen has no settings at all: it asks
        ``/api/auth/me``, that fails because nobody is signed in, and the
        frontend falls back to the title hard-coded in the JavaScript. The
        configured title has therefore never appeared there; the logo made it
        obvious.

        Deliberately *not* the whole of ``public_settings`` — the password
        policy is in there, and describing the password rules to anyone who
        loads the page gives away more than a name and a picture. This returns
        only what is already visible to anyone looking at the sign-in screen.
        """
        requires_auth = False

        def get(self):
            conn = get_db()
            try:
                s = public_settings(conn)
            finally:
                conn.close()
            # An explicit whitelist, not the whole of public_settings: this
            # endpoint answers an anonymous caller, so every key added to it is
            # a deliberate decision to make something public.
            #
            # `self_service_reset` is one boolean and it has to be here,
            # because the sign-in screen is the one place the "Forgot your
            # password?" link belongs and it has no session to ask with. What
            # it does NOT expose is anything about the mail configuration
            # behind that boolean — the host, port and username are nobody's
            # business before sign-in.
            self.write_json({k: s[k] for k in
                             ("app_title", "app_subtitle", "logo_etag",
                              "logo_position", "hide_app_title",
                              "self_service_reset", "password_policy")})

    class BrandingLogoHandler(BaseAPIHandler):
        """The logo image itself.

        **Deliberately readable without a session.** The sign-in screen shows
        it, and that page is by definition unauthenticated. This is a narrow,
        considered exception to "require a valid session for API endpoints":
        what it exposes is a brand mark a SuperAdmin chose to display on the
        public sign-in page — no record, no name, nothing about the data the
        application holds. Uploading and removing stay behind SuperAdmin.

        The response is always one of two content types, both established by
        inspecting the bytes at upload rather than trusting the browser.
        """
        requires_auth = False

        def get(self):
            conn = get_db()
            try:
                row = get_branding(conn)
                if not row or row["logo_data"] is None:
                    return self.fail(404, "No logo has been uploaded.")
                data, ctype = bytes(row["logo_data"]), row["content_type"]
                etag = row["etag"]
            finally:
                conn.close()

            # Revalidate rather than cache outright: a SuperAdmin who replaces
            # the logo should see the new one at once. A 304 is cheap, and a
            # stale logo looks exactly like a bug.
            self.set_header("ETag", f'"{etag}"')
            self.set_header("Cache-Control", "no-cache, must-revalidate")
            if self.request.headers.get("If-None-Match", "").strip('"') == etag:
                self.set_status(304)
                return self.finish()

            self.set_header("Content-Type", ctype or "image/png")
            self.set_header("X-Content-Type-Options", "nosniff")
            self.set_header("Content-Security-Policy", "default-src 'none'")
            self.finish(data)

    class BrandingHandler(BaseAPIHandler):
        """Upload, describe and remove the logo. SuperAdmin only, like Preferences."""
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def get(self):
            conn = get_db()
            try:
                self.write_json({"logo": branding_summary(get_branding(conn)),
                                 "max_bytes": MAX_LOGO_BYTES,
                                 "max_dimension": MAX_LOGO_DIMENSION,
                                 "positions": LOGO_POSITIONS})
            finally:
                conn.close()

        def post(self):
            uploaded = self.request.files.get("file")
            if not uploaded:
                return self.fail(400, "Choose a .png or .jpg file to upload.")
            f = uploaded[0]
            body = f["body"]
            try:
                content_type, width, height = validate_logo(body)
            except ImageRejected as e:
                return self.fail(400, str(e))

            filename = (f.get("filename") or "logo")[:200]
            etag = hashlib.sha256(body).hexdigest()[:32]
            now = iso(utcnow())
            conn = get_db()
            try:
                had_one = get_branding(conn) is not None
                # One row, always id 1: replacing the logo overwrites it rather
                # than accumulating versions. Unlike a resource, there is no
                # history worth keeping for a piece of decoration.
                if had_one:
                    conn.execute(
                        """UPDATE app_branding
                              SET logo_data=?, content_type=?, filename=?,
                                  size_bytes=?, width=?, height=?, etag=?,
                                  uploaded_at=?, uploaded_by=?
                            WHERE branding_id=1""",
                        (body, content_type, filename, len(body), width, height,
                         etag, now, self.current_user["user_id"]))
                else:
                    conn.execute(
                        """INSERT INTO app_branding
                             (branding_id, logo_data, content_type, filename,
                              size_bytes, width, height, etag, uploaded_at,
                              uploaded_by)
                           VALUES (1,?,?,?,?,?,?,?,?,?)""",
                        (body, content_type, filename, len(body), width, height,
                         etag, now, self.current_user["user_id"]))
                # The bytes are never written to the audit log — only the fact
                # of the change and enough detail to identify what was put
                # there. An audit row is not a file store.
                audit.record_event(
                    conn, self.current_user,
                    audit.UPDATE if had_one else audit.CREATE,
                    entity_type="branding", entity_id="logo",
                    summary=("Replaced the logo" if had_one else "Uploaded a logo")
                            + f" ({filename}, {width}x{height}, {len(body)//1024} KB)",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                summary = branding_summary(get_branding(conn))
            finally:
                conn.close()
            self.write_json({"logo": summary})

        def delete(self):
            conn = get_db()
            try:
                row = get_branding(conn)
                if not row:
                    return self.fail(404, "There is no logo to remove.")
                conn.execute("DELETE FROM app_branding WHERE branding_id=1")
                audit.record_event(
                    conn, self.current_user, audit.DELETE,
                    entity_type="branding", entity_id="logo",
                    summary=f"Removed the logo ({row['filename'] or 'logo'})",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
            finally:
                conn.close()
            self.write_json({"logo": None})

    class ReleasesHandler(BaseAPIHandler):
        """Published notes are readable by everyone; drafts and editing are SuperAdmin."""
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.SUPERADMIN
        allow_when_password_expired = False

        def get(self):
            include_drafts = permissions.at_least(self.current_user["role"],
                                                  permissions.SUPERADMIN)
            conn = get_db()
            where = "" if include_drafts else "WHERE is_published = 1"
            rows = conn.execute(
                f"SELECT * FROM app_release {where} ORDER BY sort_key DESC, version DESC"
            ).fetchall()
            conn.close()
            self.write_json({"releases": rows_to_list(rows),
                             "current_version": VERSION})

        def post(self):
            data = self.get_json_body()
            ver = (data.get("version") or "").strip()
            if not ver:
                return self.fail(400, "A version number is required.")
            conn = get_db()
            try:
                if conn.execute("SELECT 1 FROM app_release WHERE version=?", (ver,)).fetchone():
                    return self.fail(409, f"Release {ver} already exists.")
                now = iso(utcnow())
                top = conn.execute("SELECT COALESCE(MAX(sort_key),0) AS m FROM app_release").fetchone()
                conn.execute(
                    """INSERT INTO app_release
                         (version, title, notes, released_date, is_published, sort_key,
                          created_date, updated_date, created_by, updated_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (ver, data.get("title"), data.get("notes"),
                     data.get("released_date") or now[:10],
                     1 if data.get("is_published") else 0, int(top["m"]) + 100,
                     now, now, self.current_user["user_id"], self.current_user["user_id"]))
                self.audit_create(conn, "release", ver, {
                    "version": ver, "title": data.get("title"),
                    "is_published": 1 if data.get("is_published") else 0})
                conn.commit()
                row = conn.execute("SELECT * FROM app_release WHERE version=?", (ver,)).fetchone()
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(row_to_dict(row))

    class ReleaseHandler(BaseAPIHandler):
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.SUPERADMIN

        def put(self, ver):
            data = self.get_json_body()
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM app_release WHERE version=?",
                                      (ver,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                before = dict(before)
                fields, vals = [], []
                for key in ("title", "notes", "released_date"):
                    if key in data:
                        fields.append(f"{key}=?")
                        vals.append(data[key])
                if "is_published" in data:
                    fields.append("is_published=?")
                    vals.append(1 if data["is_published"] else 0)
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), self.current_user["user_id"], ver]
                    conn.execute(f"UPDATE app_release SET {','.join(fields)} WHERE version=?", vals)
                after = dict(conn.execute("SELECT * FROM app_release WHERE version=?",
                                          (ver,)).fetchone())
                self.audit_update(conn, "release", ver, before, after,
                                  fields=("title", "notes", "released_date", "is_published"))
                conn.commit()
            finally:
                conn.close()
            self.write_json(after)

        def delete(self, ver):
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM app_release WHERE version=?",
                                      (ver,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                conn.execute("DELETE FROM app_release WHERE version=?", (ver,))
                self.audit_delete(conn, "release", ver, dict(before))
                conn.commit()
            finally:
                conn.close()
            self.write_json({"deleted": ver})

    class WhatsNewHandler(BaseAPIHandler):
        """Published releases this user hasn't been shown yet, oldest first.

        Someone returning after several releases sees all of them in order
        rather than only the newest.
        """
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER
        allow_when_password_expired = True

        def get(self):
            conn = get_db()
            try:
                row = conn.execute("SELECT last_seen_release FROM app_user WHERE user_id=?",
                                   (self.current_user["user_id"],)).fetchone()
                seen = (row or {}).get("last_seen_release")
                releases = conn.execute(
                    "SELECT * FROM app_release WHERE is_published=1 "
                    "ORDER BY sort_key ASC, version ASC").fetchall()
            finally:
                conn.close()
            all_published = rows_to_list(releases)
            if seen is None:
                # Should not normally happen: accounts are stamped on creation.
                unseen = all_published
            else:
                seen_key = next((r["sort_key"] for r in all_published
                                 if r["version"] == seen), None)
                unseen = [r for r in all_published
                          if seen_key is None or r["sort_key"] > seen_key]
            self.write_json({"unseen": unseen, "current_version": VERSION})

        def post(self):
            """Mark everything published as seen ("Got it")."""
            conn = get_db()
            try:
                latest = conn.execute(
                    "SELECT version FROM app_release WHERE is_published=1 "
                    "ORDER BY sort_key DESC LIMIT 1").fetchone()
                marker = (latest or {}).get("version") or VERSION
                conn.execute("UPDATE app_user SET last_seen_release=? WHERE user_id=?",
                             (marker, self.current_user["user_id"]))
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True})

    # -----------------------------------------------------------------------
    # Resources: documents and links on systems and data flows
    # -----------------------------------------------------------------------
    RESOURCE_PARENTS = {
        "system": ("system", "system_id"),
        "data_flow": ("data_flow", "flow_id"),
        # A task's documents are the procedure somebody follows to do it: the
        # checklist, the spreadsheet template, the memo saying who signed off.
        # Nothing here is task-specific — this dict is the whole switch, and
        # `require_parent_in_view` works for any scoped table with an id.
        "task": ("task", "task_id"),
    }

    def resource_rows(conn, entity_type, entity_id):
        """Resources with their current version, never selecting the file bytes."""
        return conn.execute(
            """SELECT r.resource_id, r.entity_type, r.entity_id, r.label, r.kind,
                      r.label_locked, r.sort_order, r.description,
                      r.created_date, r.updated_date,
                      v.version_id, v.version_no, v.url, v.filename, v.content_type,
                      v.size_bytes, v.uploaded_at, v.uploaded_by, v.note,
                      u.email AS uploaded_by_email,
                      (SELECT COUNT(*) FROM resource_version rv
                        WHERE rv.resource_id = r.resource_id) AS version_count
                 FROM resource r
                 LEFT JOIN resource_version v
                        ON v.resource_id = r.resource_id AND v.is_current = 1
                 LEFT JOIN app_user u ON u.user_id = v.uploaded_by
                WHERE r.entity_type = ? AND r.entity_id = ?
                ORDER BY r.sort_order, r.resource_id""",
            (entity_type, entity_id)).fetchall()

    def require_parent_in_view(handler, conn, entity_type, entity_id, write=False):
        """Confirm the record a resource hangs off is visible in this view.

        Resource ids are scenario-unique already, because the parent's id is
        (``S3-SYS-001``). What this adds is authority: it is what lets someone
        who is only a Viewer elsewhere attach a document inside a scenario they
        were invited to edit, while still refusing them on Current
        Architecture.
        """
        table, pk = RESOURCE_PARENTS[entity_type]
        acc = handler.scenario_access(conn, write=write)
        where, params = handler.scope_sql()
        row = conn.execute(f"SELECT 1 FROM {table} WHERE {pk}=? AND {where}",
                           (entity_id,) + params).fetchone()
        if not row:
            raise scenarios.ScenarioError("Not found.", 404)
        return acc

    class ResourcesHandler(BaseAPIHandler):
        """List resources for an entity, or attach a new link/document."""
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.EDITOR
        # Inside a scenario the scenario role decides, so somebody invited as an
        # editor can attach documents even if they are a Viewer elsewhere.
        SCENARIO_AWARE = True

        def get(self, entity_type, entity_id):
            if entity_type not in RESOURCE_PARENTS:
                return self.fail(400, "Unknown entity type")
            conn = get_db()
            try:
                require_parent_in_view(self, conn, entity_type, entity_id)
            except scenarios.ScenarioError as e:
                conn.close()
                return self.fail(e.status, e.message)
            rows = resource_rows(conn, entity_type, entity_id)
            conn.close()
            out = []
            for r in rows_to_list(rows):
                r["can_preview"] = (r.get("content_type") or "") in INLINE_CONTENT_TYPES
                out.append(r)
            self.write_json(out)

        def post(self, entity_type, entity_id):
            if entity_type not in RESOURCE_PARENTS:
                return self.fail(400, "Unknown entity type")
            table, pk = RESOURCE_PARENTS[entity_type]
            probe = get_db()
            try:
                require_parent_in_view(self, probe, entity_type, entity_id, write=True)
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                probe.close()

            uploaded = self.request.files.get("file")
            if uploaded:
                label = (self.get_body_argument("label", "") or "").strip()
                description = (self.get_body_argument("description", "") or "").strip()
                kind = "file"
            else:
                data = self.get_json_body()
                label = (data.get("label") or "").strip()
                description = (data.get("description") or "").strip()
                kind = data.get("kind") or "link"
            if not label:
                return self.fail(400, "A label is required.")
            if len(description) > MAX_DESCRIPTION:
                return self.fail(400, f"The description must be "
                                      f"{MAX_DESCRIPTION} characters or fewer.")

            conn = get_db()
            try:
                if not conn.execute(f"SELECT 1 FROM {table} WHERE {pk}=?",
                                    (entity_id,)).fetchone():
                    return self.fail(404, f"No such {entity_type}.")
                now = iso(utcnow())
                top = conn.execute(
                    "SELECT COALESCE(MAX(sort_order),0) AS m FROM resource "
                    "WHERE entity_type=? AND entity_id=?",
                    (entity_type, entity_id)).fetchone()
                created = conn.execute(
                    """INSERT INTO resource
                         (entity_type, entity_id, label, kind, sort_order, description,
                          created_date, updated_date, created_by, updated_by, scenario_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING resource_id""",
                    (entity_type, entity_id, label, kind, int(top["m"]) + 10,
                     (description or None)[:MAX_DESCRIPTION] if description else None,
                     now, now, self.current_user["user_id"],
                     self.current_user["user_id"], self.scenario_id)).fetchone()
                rid = created["resource_id"]

                if kind == "file":
                    err = self._store_file(conn, rid, 1)
                    if err:
                        return self.fail(400, err)
                else:
                    url = (self.get_json_body().get("url") or "").strip()
                    if not url:
                        return self.fail(400, "A link URL is required.")
                    if not re.match(r'^https?://', url, re.I):
                        return self.fail(400, "Links must start with http:// or https://")
                    conn.execute(
                        """INSERT INTO resource_version
                             (resource_id, version_no, is_current, url, uploaded_at, uploaded_by)
                           VALUES (?,1,1,?,?,?)""",
                        (rid, url, now, self.current_user["user_id"]))

                self.audit_create(conn, f"{entity_type}_resource", rid,
                                  {"label": label, "kind": kind,
                                   "attached_to": entity_id})
                conn.commit()
                rows = resource_rows(conn, entity_type, entity_id)
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(rows_to_list(rows))

        def _store_file(self, conn, resource_id, version_no, note=None):
            """Write an uploaded file as a new version. Returns an error string or None."""
            uploaded = self.request.files.get("file")
            if not uploaded:
                return "No file was included."
            f = uploaded[0]
            body = f["body"]
            if len(body) > MAX_UPLOAD_BYTES:
                return (f"That file is {len(body)/1024/1024:.1f} MB. "
                        f"The limit is {MAX_UPLOAD_BYTES//1024//1024} MB.")
            if not body:
                return "That file is empty."
            conn.execute("UPDATE resource_version SET is_current=0 WHERE resource_id=?",
                         (resource_id,))
            conn.execute(
                """INSERT INTO resource_version
                     (resource_id, version_no, is_current, filename, content_type,
                      size_bytes, file_data, uploaded_at, uploaded_by, note)
                   VALUES (?,?,1,?,?,?,?,?,?,?)""",
                (resource_id, version_no, f["filename"],
                 f["content_type"] or "application/octet-stream",
                 len(body), body, iso(utcnow()), self.current_user["user_id"], note))
            return None

    class ResourceHandler(BaseAPIHandler):
        """Relabel a resource, upload a new version over it, or remove it."""
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.EDITOR
        SCENARIO_AWARE = True

        def _load(self, conn, rid):
            return conn.execute("SELECT * FROM resource WHERE resource_id=?", (rid,)).fetchone()

        def post(self, rid):
            """Upload a replacement document, keeping the previous version."""
            conn = get_db()
            try:
                r = self._load(conn, rid)
                if not r:
                    return self.fail(404, "Not found")
                if r["kind"] != "file":
                    return self.fail(400, "This resource is a link, not a document.")
                top = conn.execute(
                    "SELECT COALESCE(MAX(version_no),0) AS m FROM resource_version "
                    "WHERE resource_id=?", (rid,)).fetchone()
                new_no = int(top["m"]) + 1
                helper = ResourcesHandler._store_file
                err = helper(self, conn, int(rid), new_no,
                             note=self.get_body_argument("note", None))
                if err:
                    return self.fail(400, err)
                audit.record_event(
                    conn, self.current_user, audit.UPDATE,
                    f"{r['entity_type']}_resource", rid,
                    changes={"document": (f"version {new_no - 1}", f"version {new_no}")},
                    summary=f"New version uploaded for '{r['label']}'",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                rows = resource_rows(conn, r["entity_type"], r["entity_id"])
            finally:
                conn.close()
            self.write_json(rows_to_list(rows))

        def put(self, rid):
            data = self.get_json_body()
            conn = get_db()
            try:
                r = self._load(conn, rid)
                if not r:
                    return self.fail(404, "Not found")
                before = dict(r)
                fields, vals = [], []
                if "description" in data:
                    desc = (data["description"] or "").strip()
                    if len(desc) > MAX_DESCRIPTION:
                        return self.fail(400, f"The description must be "
                                              f"{MAX_DESCRIPTION} characters or fewer.")
                    fields.append("description=?")
                    vals.append(desc or None)
                if "label" in data:
                    if r["label_locked"]:
                        return self.fail(400, "This resource's label is fixed by the "
                                              "system and cannot be changed.")
                    if not (data["label"] or "").strip():
                        return self.fail(400, "A label is required.")
                    fields.append("label=?")
                    vals.append(data["label"].strip())
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), self.current_user["user_id"], rid]
                    conn.execute(f"UPDATE resource SET {','.join(fields)} WHERE resource_id=?", vals)
                if "url" in data and r["kind"] == "link":
                    url = (data["url"] or "").strip()
                    if not re.match(r'^https?://', url, re.I):
                        return self.fail(400, "Links must start with http:// or https://")
                    top = conn.execute(
                        "SELECT COALESCE(MAX(version_no),0) AS m FROM resource_version "
                        "WHERE resource_id=?", (rid,)).fetchone()
                    conn.execute("UPDATE resource_version SET is_current=0 WHERE resource_id=?",
                                 (rid,))
                    conn.execute(
                        """INSERT INTO resource_version
                             (resource_id, version_no, is_current, url, uploaded_at, uploaded_by)
                           VALUES (?,?,1,?,?,?)""",
                        (rid, int(top["m"]) + 1, url, iso(utcnow()),
                         self.current_user["user_id"]))
                after = dict(self._load(conn, rid))
                self.audit_update(conn, f"{r['entity_type']}_resource", rid, before, after,
                                  fields=("label", "description"))
                conn.commit()
                rows = resource_rows(conn, r["entity_type"], r["entity_id"])
            finally:
                conn.close()
            self.write_json(rows_to_list(rows))

        def delete(self, rid):
            conn = get_db()
            try:
                r = self._load(conn, rid)
                if not r:
                    return self.fail(404, "Not found")
                # A locked slot is one the record cannot do without. That is
                # true of a Manual Entry system's field list, which is why the
                # system creates it; it is not true of a field list added to a
                # data flow by choice, which can be removed again.
                if r["label_locked"] and r["entity_type"] != "data_flow":
                    return self.fail(400, "This resource is maintained by the system "
                                          "and cannot be removed.")
                conn.execute("DELETE FROM resource_version WHERE resource_id=?", (rid,))
                conn.execute("DELETE FROM resource WHERE resource_id=?", (rid,))
                self.audit_delete(conn, f"{r['entity_type']}_resource", rid, dict(r))
                conn.commit()
                rows = resource_rows(conn, r["entity_type"], r["entity_id"])
            finally:
                conn.close()
            self.write_json(rows_to_list(rows))

    class ResourceClearHandler(BaseAPIHandler):
        """Empty a resource without removing the slot it lives in.

        A Manual Entry system's field list is required, so it cannot simply be
        deleted — but the document attached to it often needs replacing with
        nothing at all. Clearing returns the slot to how it looks before
        anything has been added.

        Earlier versions are kept rather than destroyed: the audit trail should
        stay honest about what was once attached, and a document cleared by
        mistake is then recoverable.
        """
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.EDITOR
        SCENARIO_AWARE = True

        def post(self, rid):
            conn = get_db()
            try:
                r = conn.execute("SELECT * FROM resource WHERE resource_id=?",
                                 (rid,)).fetchone()
                if not r:
                    return self.fail(404, "Not found")
                try:
                    require_parent_in_view(self, conn, r["entity_type"],
                                           r["entity_id"], write=True)
                except scenarios.ScenarioError as e:
                    return self.fail(e.status, e.message)
                current = conn.execute(
                    "SELECT * FROM resource_version WHERE resource_id=? AND is_current=1",
                    (rid,)).fetchone()
                if not current:
                    return self.fail(400, "There is nothing attached to clear.")
                # The version stays; it simply stops being the current one.
                conn.execute("UPDATE resource_version SET is_current=0 WHERE resource_id=?",
                             (rid,))
                audit.record_event(
                    conn, self.current_user, "update", "resource", rid,
                    summary=f"Cleared the attachment on '{r['label']}' "
                            f"(earlier versions kept)",
                    scenario_id=self.scenario_id,
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                self.write_json({"resource_id": int(rid), "cleared": True})
            finally:
                conn.close()

    class FieldListHandler(BaseAPIHandler):
        """Add the fixed Field List slot to a data flow.

        Manual Entry systems get one automatically because they cannot be
        described without it. On a data flow it is optional and added on
        request, which is why it can also be removed again.
        """
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.EDITOR
        SCENARIO_AWARE = True

        def post(self, entity_type, entity_id):
            if entity_type not in ("system", "data_flow"):
                return self.fail(400, "Unknown entity type")
            conn = get_db()
            try:
                try:
                    require_parent_in_view(self, conn, entity_type, entity_id, write=True)
                except scenarios.ScenarioError as e:
                    return self.fail(e.status, e.message)
                ensure_field_list_resource(self, conn, entity_id, entity_type)
                conn.commit()
                rows = resource_rows(conn, entity_type, entity_id)
                self.write_json(rows_to_list(rows))
            finally:
                conn.close()

    class ResourceFileHandler(BaseAPIHandler):
        """Serve a stored document, inline for preview or as a download."""
        SCENARIO_AWARE = True
        READ_ROLE = permissions.VIEWER

        def get(self, rid):
            want_download = self.get_argument("download", "0") == "1"
            version_id = self.get_argument("version", None)
            conn = get_db()
            try:
                if version_id:
                    row = conn.execute(
                        "SELECT * FROM resource_version WHERE version_id=? AND resource_id=?",
                        (version_id, rid)).fetchone()
                else:
                    row = conn.execute(
                        "SELECT * FROM resource_version WHERE resource_id=? AND is_current=1",
                        (rid,)).fetchone()
                if not row or row["file_data"] is None:
                    return self.fail(404, "No document stored for this resource.")
                data = bytes(row["file_data"])
                ctype = row["content_type"] or "application/octet-stream"
                filename = row["filename"] or "document"
            finally:
                conn.close()

            # Only ever render a narrow set of types inline. Anything else is
            # sent as an attachment, so an uploaded HTML or script file can't be
            # executed in the context of this site.
            inline = (not want_download) and ctype in INLINE_CONTENT_TYPES
            safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename)
            self.set_header("Content-Type", ctype if inline else "application/octet-stream")
            self.set_header("Content-Disposition",
                            f'{"inline" if inline else "attachment"}; filename="{safe_name}"')
            self.set_header("X-Content-Type-Options", "nosniff")
            self.set_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.finish(data)

    class ResourceVersionsHandler(BaseAPIHandler):
        SCENARIO_AWARE = True
        READ_ROLE = permissions.VIEWER

        def get(self, rid):
            conn = get_db()
            rows = conn.execute(
                """SELECT v.version_id, v.version_no, v.is_current, v.url, v.filename,
                          v.content_type, v.size_bytes, v.uploaded_at, v.note,
                          u.email AS uploaded_by_email
                     FROM resource_version v
                     LEFT JOIN app_user u ON u.user_id = v.uploaded_by
                    WHERE v.resource_id=? ORDER BY v.version_no DESC""", (rid,)).fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

    # -----------------------------------------------------------------------
    # Schema upload (.sql) with revertible snapshots
    # -----------------------------------------------------------------------
    def capture_schema(conn, system_id):
        """Everything currently stored for a system's schema, as plain dicts."""
        tables = rows_to_list(conn.execute(
            "SELECT * FROM schema_table WHERE system_id=? ORDER BY table_id",
            (system_id,)).fetchall())
        ids = [t["table_id"] for t in tables]
        columns = []
        if ids:
            ph = ",".join("?" for _ in ids)
            columns = rows_to_list(conn.execute(
                f"SELECT * FROM schema_column WHERE table_id IN ({ph}) ORDER BY table_id, ordinal",
                ids).fetchall())
        return tables, columns

    def write_schema(conn, system_id, tables, columns, scenario_id=None):
        """Replace a system's schema with the supplied tables and columns.

        ``scenario_id`` tags the new rows so they belong to that scenario.
        Without it they would carry a scenario system id but a NULL scenario,
        and the read-through check would not find them — the upload would
        appear to do nothing.
        """
        existing = [r["table_id"] for r in conn.execute(
            "SELECT table_id FROM schema_table WHERE system_id=?", (system_id,)).fetchall()]
        if existing:
            ph = ",".join("?" for _ in existing)
            conn.execute(f"DELETE FROM schema_column WHERE table_id IN ({ph})", existing)
            conn.execute("DELETE FROM schema_table WHERE system_id=?", (system_id,))

        now = iso(utcnow())
        t_cols = ["scenario_id", "table_id", "system_id", "schema_name", "table_name", "primary_key",
                  "description", "x_pos", "y_pos", "data_classification", "contains_pii",
                  "ferpa_protected", "data_owner_id", "data_steward_id",
                  "retention_schedule", "access_restrictions", "legal_authority",
                  "business_meaning", "notes", "created_date", "updated_date"]
        for t in tables:
            row = dict(t)
            row["system_id"] = system_id
            row["scenario_id"] = scenario_id
            row.setdefault("created_date", now)
            row["updated_date"] = now
            conn.execute(
                f"INSERT INTO schema_table ({','.join(t_cols)}) "
                f"VALUES ({','.join('?' for _ in t_cols)})",
                tuple(row.get(c) for c in t_cols))

        c_cols = ["scenario_id", "column_id", "table_id", "column_name", "ordinal", "data_type",
                  "is_nullable", "is_identity", "is_pk", "is_fk", "default_value",
                  "fk_target_schema", "fk_target_table", "fk_target_column",
                  "fk_target_table_id", "fk_constraint", "data_classification",
                  "contains_pii", "ferpa_protected", "data_owner_id", "data_steward_id",
                  "retention_schedule", "access_restrictions", "legal_authority",
                  "business_meaning", "notes", "created_date", "updated_date"]
        for c in columns:
            row = dict(c)
            row["scenario_id"] = scenario_id
            row.setdefault("created_date", now)
            row["updated_date"] = now
            conn.execute(
                f"INSERT INTO schema_column ({','.join(c_cols)}) "
                f"VALUES ({','.join('?' for _ in c_cols)})",
                tuple(row.get(col) for col in c_cols))

    class SchemaUploadHandler(BaseAPIHandler):
        """Replace a data store's schema from an uploaded SQL Server script.

        Governance already entered against a table or column is carried forward
        by name, and the previous schema is snapshotted so the upload can be
        reverted. One audit event summarizes the change rather than thousands of
        per-column rows.
        """
        READ_ROLE = permissions.EDITOR
        WRITE_ROLE = permissions.EDITOR
        # Uploading inside a scenario replaces that scenario's schema only;
        # Current Architecture keeps whatever it had.
        SCENARIO_AWARE = True

        def post(self, system_id):
            uploaded = self.request.files.get("file")
            if not uploaded:
                return self.fail(400, "No .sql file was included.")
            # Checked before any parsing: a non-member must not be able to tell
            # whether the system exists by how long the request takes.
            probe = get_db()
            try:
                self.scenario_access(probe, write=True)
                where, params = self.scope_sql()
                owns = probe.execute(
                    f"SELECT 1 FROM system WHERE system_id=? AND {where}",
                    (system_id,) + params).fetchone()
                if not owns:
                    return self.fail(404, "Not found")
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                probe.close()
            f = uploaded[0]
            if len(f["body"]) > MAX_UPLOAD_BYTES:
                return self.fail(400, f"That file is larger than "
                                      f"{MAX_UPLOAD_BYTES//1024//1024} MB.")
            try:
                sql_text = f["body"].decode("utf-8", errors="replace")
            except Exception:
                return self.fail(400, "The file could not be read as text.")

            conn = get_db()
            try:
                system = conn.execute("SELECT * FROM system WHERE system_id=?",
                                      (system_id,)).fetchone()
                if not system:
                    return self.fail(404, "No such system.")

                # Identifiers must be unique across the whole table, not just
                # within this system: the parser's default TBL-0001 collides with
                # whichever system already owns that id. Derive the prefix from
                # the system so two systems can never clash.
                token = re.sub(r'[^A-Za-z0-9]', '', system_id).upper()[-8:] or "SYS"
                try:
                    parsed = schema_import.parse_tsql(
                        sql_text,
                        table_prefix=f"{token}T",
                        column_prefix=f"{token}C")
                except schema_import.SchemaParseError as e:
                    return self.fail(400, str(e))

                # Belt and braces: if a generated id somehow exists under another
                # system, fall back to a time-stamped prefix rather than failing
                # mid-write with a constraint violation.
                clash = conn.execute(
                    "SELECT 1 FROM schema_table WHERE table_id = ? AND system_id <> ?",
                    (parsed["tables"][0]["table_id"], system_id)).fetchone()
                if clash:
                    stamp = utcnow().strftime("%y%m%d%H%M%S")
                    parsed = schema_import.parse_tsql(
                        sql_text,
                        table_prefix=f"{token}T{stamp}",
                        column_prefix=f"{token}C{stamp}")

                old_tables, old_columns = capture_schema(conn, system_id)
                summary = schema_import.carry_governance(parsed, old_tables, old_columns)

                conn.execute(
                    """INSERT INTO schema_snapshot
                         (system_id, created_at, created_by, source_filename, source_sql,
                          snapshot_json, table_count, column_count, summary, is_current)
                       VALUES (?,?,?,?,?,?,?,?,?,0)""",
                    (system_id, iso(utcnow()), self.current_user["user_id"],
                     f["filename"], sql_text,
                     json.dumps({"tables": old_tables, "columns": old_columns}, default=str),
                     len(old_tables), len(old_columns),
                     f"Schema before upload of {f['filename']}"))

                write_schema(conn, system_id, parsed["tables"], parsed["columns"],
                             scenario_id=self.scenario_id)
                mark_modified(conn, "system", "system_id", system_id, self.scenario_id)

                message = (f"Schema replaced from {f['filename']}: "
                           f"{summary['matched_tables']} table(s) matched, "
                           f"{summary['new_tables']} added, "
                           f"{summary['removed_tables']} removed; "
                           f"{summary['carried_values']} governance value(s) carried forward")
                audit.record_event(
                    conn, self.current_user, audit.UPDATE, "system_schema", system_id,
                    summary=message, ip_address=self.client_ip(),
                    user_agent=self.user_agent())
                conn.commit()
            finally:
                conn.close()

            self.write_json({"ok": True, "summary": summary,
                             "stats": parsed["stats"],
                             "warnings": parsed.get("warnings", []),
                             "message": message})

    class SchemaSnapshotsHandler(BaseAPIHandler):
        SCENARIO_AWARE = True
        READ_ROLE = permissions.EDITOR

        def get(self, system_id):
            conn = get_db()
            rows = conn.execute(
                """SELECT s.snapshot_id, s.created_at, s.source_filename, s.table_count,
                          s.column_count, s.summary, u.email AS created_by_email
                     FROM schema_snapshot s
                     LEFT JOIN app_user u ON u.user_id = s.created_by
                    WHERE s.system_id=? ORDER BY s.snapshot_id DESC""",
                (system_id,)).fetchall()
            conn.close()
            self.write_json(rows_to_list(rows))

    class SchemaRevertHandler(BaseAPIHandler):
        SCENARIO_AWARE = True
        # Reverting discards work, so it stays with SuperAdmins even though
        # uploading is now open to Editors.
        READ_ROLE = permissions.EDITOR
        WRITE_ROLE = permissions.SUPERADMIN

        def post(self, snapshot_id):
            conn = get_db()
            try:
                snap = conn.execute("SELECT * FROM schema_snapshot WHERE snapshot_id=?",
                                    (snapshot_id,)).fetchone()
                if not snap:
                    return self.fail(404, "Snapshot not found.")
                payload = json.loads(snap["snapshot_json"] or "{}")
                tables = payload.get("tables", [])
                columns = payload.get("columns", [])
                system_id = snap["system_id"]

                # Snapshot the current state first, so a revert is itself revertible.
                cur_t, cur_c = capture_schema(conn, system_id)
                conn.execute(
                    """INSERT INTO schema_snapshot
                         (system_id, created_at, created_by, source_filename, source_sql,
                          snapshot_json, table_count, column_count, summary, is_current)
                       VALUES (?,?,?,?,?,?,?,?,?,0)""",
                    (system_id, iso(utcnow()), self.current_user["user_id"],
                     None, None,
                     json.dumps({"tables": cur_t, "columns": cur_c}, default=str),
                     len(cur_t), len(cur_c),
                     f"Schema before reverting to snapshot {snapshot_id}"))

                write_schema(conn, system_id, tables, columns)
                message = (f"Schema reverted to the snapshot taken "
                           f"{str(snap['created_at'])[:16].replace('T', ' ')} "
                           f"({len(tables)} tables, {len(columns)} columns)")

                # Tell whoever uploaded the schema being undone, unless that is
                # the person doing the reverting.
                undone = conn.execute(
                    "SELECT created_by, source_filename FROM schema_snapshot "
                    "WHERE system_id=? AND snapshot_id > ? ORDER BY snapshot_id LIMIT 1",
                    (system_id, snapshot_id)).fetchone()
                uploader = (undone or {}).get("created_by")
                if uploader and uploader != self.current_user["user_id"]:
                    sysname = conn.execute("SELECT name FROM system WHERE system_id=?",
                                           (system_id,)).fetchone()["name"]
                    notify_user(
                        conn, uploader, "schema_reverted",
                        f"A schema you uploaded was reverted",
                        body=(f"The schema you uploaded for {sysname} has been "
                              f"rolled back to an earlier version."),
                        entity_type="system", entity_id=system_id,
                        from_email=self.current_user["email"])
                audit.record_event(conn, self.current_user, audit.UPDATE,
                                   "system_schema", system_id, summary=message,
                                   ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True, "message": message})

    # -----------------------------------------------------------------------
    # Reverting an audited change
    # -----------------------------------------------------------------------
    # Only catalog data, and only field updates. Creates and deletes are not
    # revertible yet, and schema uploads revert through their own snapshots.
    REVERTIBLE_ENTITIES = {
        "system": ("system", "system_id"),
        "data_flow": ("data_flow", "flow_id"),
        "data_element": ("data_element", "element_id"),
        "contributor": ("contributor", "contributor_id"),
        "schema_table": ("schema_table", "table_id"),
        "schema_column": ("schema_column", "column_id"),
    }

    class AuditRevertHandler(BaseAPIHandler):
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def post(self, event_id):
            force = (self.get_json_body() or {}).get("force", False)
            conn = get_db()
            try:
                event = conn.execute("SELECT * FROM audit_event WHERE event_id=?",
                                     (event_id,)).fetchone()
                if not event:
                    return self.fail(404, "Audit entry not found.")
                if event["action"] != audit.UPDATE:
                    return self.fail(400, "Only updates can be reverted. Creations and "
                                          "deletions are not revertible.")
                target = REVERTIBLE_ENTITIES.get(event["entity_type"])
                if not target:
                    return self.fail(400, f"Changes to '{event['entity_type']}' cannot be "
                                          f"reverted from here.")
                table, pk = target
                changes = conn.execute(
                    "SELECT * FROM audit_field_change WHERE event_id=? ORDER BY change_id",
                    (event_id,)).fetchall()
                if not changes:
                    return self.fail(400, "This entry records no field changes.")

                row = conn.execute(f"SELECT * FROM {table} WHERE {pk}=?",
                                   (event["entity_id"],)).fetchone()
                if not row:
                    return self.fail(404, "The record this entry refers to no longer exists.")
                current = dict(row)

                # If a field no longer holds the value this event set, something
                # later changed it and reverting would silently discard that.
                conflicts = []
                for c in changes:
                    field = c["field_name"]
                    if field not in current:
                        continue
                    now_value = current.get(field)
                    expected = c["new_value"]
                    if (now_value is None and expected not in (None, "")) or \
                       (now_value is not None and str(now_value) != str(expected)):
                        conflicts.append({
                            "field": field,
                            "expected": expected,
                            "current": None if now_value is None else str(now_value),
                        })
                if conflicts and not force:
                    return self.fail(
                        409,
                        "Some of these fields have changed since this entry. Reverting "
                        "will discard those newer changes.",
                        conflicts=conflicts)

                before = dict(current)
                fields, vals = [], []
                for c in changes:
                    field = c["field_name"]
                    if field not in current:
                        continue
                    fields.append(f"{field}=?")
                    vals.append(c["old_value"])
                if not fields:
                    return self.fail(400, "None of these fields exist on the record.")
                fields += ["updated_date=?", "updated_by=?"]
                vals += [iso(utcnow()), self.current_user["user_id"], event["entity_id"]]
                conn.execute(f"UPDATE {table} SET {','.join(fields)} WHERE {pk}=?", vals)

                after = dict(conn.execute(f"SELECT * FROM {table} WHERE {pk}=?",
                                          (event["entity_id"],)).fetchone())
                # The revert is recorded as a new event; the original entry is
                # never altered, so the log stays append-only.
                audit.record_event(
                    conn, self.current_user, audit.UPDATE, event["entity_type"],
                    event["entity_id"],
                    changes=audit.diff_records(before, after,
                                               [c["field_name"] for c in changes]),
                    summary=(f"Reverted change #{event_id} originally made by "
                             f"{event['actor_email']} on "
                             f"{str(event['occurred_at'])[:16].replace('T', ' ')}"
                             + (" (discarding later changes)" if conflicts else "")),
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                # Let the original author know their change was undone. Skipped
                # when someone reverts their own change.
                original_author = event["actor_user_id"]
                if original_author and original_author != self.current_user["user_id"]:
                    fields = [{"field": c["field_name"],
                               "reverted_from": c["new_value"],
                               "restored_to": c["old_value"]} for c in changes]
                    notify_user(
                        conn, original_author, "change_reverted",
                        "A change you made was reverted",
                        body=(f"Your change to {event['entity_type']} "
                              f"{event['entity_id']} on "
                              f"{str(event['occurred_at'])[:16].replace('T', ' ')} "
                              f"was reverted by {self.current_user['email']}."),
                        detail={"fields": fields},
                        entity_type=event["entity_type"],
                        entity_id=event["entity_id"],
                        related_event_id=int(event_id),
                        from_email=self.current_user["email"])
                conn.commit()
            finally:
                conn.close()
            self.write_json({"ok": True, "reverted_event": int(event_id),
                             "discarded_conflicts": conflicts})

    # -----------------------------------------------------------------------
    # System types (everyone reads; SuperAdmin edits)
    # -----------------------------------------------------------------------
    class SystemTypesHandler(BaseAPIHandler):
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.SUPERADMIN

        def get(self):
            conn = get_db()
            try:
                types = get_system_types(conn)
                counts = {r["system_type"]: r["n"] for r in conn.execute(
                    "SELECT system_type, COUNT(*) AS n FROM system "
                    "GROUP BY system_type").fetchall()}
            finally:
                conn.close()
            for t in types:
                t["in_use"] = counts.get(t["type_name"], 0)
            self.write_json({"types": types, "palette": TYPE_PALETTE})

        def post(self):
            data = self.get_json_body()
            name = (data.get("type_name") or "").strip()
            if not name:
                return self.fail(400, "A type name is required.")
            swatch = TYPE_PALETTE.get(data.get("palette") or "slate",
                                      TYPE_PALETTE["slate"])
            conn = get_db()
            try:
                if conn.execute("SELECT 1 FROM system_type WHERE type_name=?",
                                (name,)).fetchone():
                    return self.fail(409, f"A type called '{name}' already exists.")
                top = conn.execute(
                    "SELECT COALESCE(MAX(sort_order),0) AS m FROM system_type").fetchone()
                now = iso(utcnow())
                conn.execute(
                    """INSERT INTO system_type
                         (type_name, description, fill_color, stroke_color,
                          text_color, sort_order, created_date, updated_date, updated_by)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (name, data.get("description"), swatch["fill"], swatch["stroke"],
                     swatch["text"], int(top["m"]) + 10, now, now,
                     self.current_user["user_id"]))
                self.audit_create(conn, "system_type", name,
                                  {"type_name": name,
                                   "description": data.get("description")})
                conn.commit()
                types = get_system_types(conn)
            finally:
                conn.close()
            self.set_status(201)
            self.write_json({"types": types, "palette": TYPE_PALETTE})

    class SystemTypeHandler(BaseAPIHandler):
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def put(self, type_name):
            data = self.get_json_body()
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM system_type WHERE type_name=?",
                                      (type_name,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                before = dict(before)
                new_name = (data.get("type_name") or type_name).strip()

                fields, vals = [], []
                if "description" in data:
                    fields.append("description=?")
                    vals.append(data["description"])
                if data.get("palette") in TYPE_PALETTE:
                    sw = TYPE_PALETTE[data["palette"]]
                    fields += ["fill_color=?", "stroke_color=?", "text_color=?"]
                    vals += [sw["fill"], sw["stroke"], sw["text"]]
                if fields:
                    fields += ["updated_date=?", "updated_by=?"]
                    vals += [iso(utcnow()), self.current_user["user_id"], type_name]
                    conn.execute(
                        f"UPDATE system_type SET {','.join(fields)} WHERE type_name=?",
                        vals)

                renamed = 0
                if new_name != type_name:
                    if conn.execute("SELECT 1 FROM system_type WHERE type_name=?",
                                    (new_name,)).fetchone():
                        return self.fail(409, f"A type called '{new_name}' already exists.")
                    # Systems store the type by name, so a rename has to carry
                    # across to every system using it or they would be orphaned.
                    renamed = conn.execute(
                        "SELECT COUNT(*) FROM system WHERE system_type=?",
                        (type_name,)).fetchone()[0]
                    conn.execute("UPDATE system_type SET type_name=? WHERE type_name=?",
                                 (new_name, type_name))
                    conn.execute("UPDATE system SET system_type=? WHERE system_type=?",
                                 (new_name, type_name))

                after = dict(conn.execute("SELECT * FROM system_type WHERE type_name=?",
                                          (new_name,)).fetchone())
                self.audit_update(
                    conn, "system_type", new_name, before, after,
                    fields=("type_name", "description", "fill_color", "stroke_color"))
                if renamed:
                    audit.record_event(
                        conn, self.current_user, audit.UPDATE, "system", None,
                        summary=(f"System type renamed '{type_name}' -> '{new_name}'; "
                                 f"{renamed} system(s) updated"),
                        ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                types = get_system_types(conn)
            finally:
                conn.close()
            self.write_json({"types": types, "palette": TYPE_PALETTE,
                             "renamed_systems": renamed})

        def delete(self, type_name):
            conn = get_db()
            try:
                before = conn.execute("SELECT * FROM system_type WHERE type_name=?",
                                      (type_name,)).fetchone()
                if not before:
                    return self.fail(404, "Not found")
                in_use = conn.execute("SELECT COUNT(*) FROM system WHERE system_type=?",
                                      (type_name,)).fetchone()[0]
                if in_use:
                    return self.fail(
                        409, f"{in_use} system(s) use the '{type_name}' type. "
                             f"Change them to another type first.")
                if conn.execute("SELECT COUNT(*) FROM system_type").fetchone()[0] <= 1:
                    return self.fail(409, "At least one system type must remain.")
                conn.execute("DELETE FROM system_type WHERE type_name=?", (type_name,))
                self.audit_delete(conn, "system_type", type_name, dict(before))
                conn.commit()
                types = get_system_types(conn)
            finally:
                conn.close()
            self.write_json({"types": types, "palette": TYPE_PALETTE})

    class FlowRouteHandler(BaseAPIHandler):
        """Where a data flow's arrow attaches, and how it bends.

        Separate from the flow's own fields because this is layout, not
        content: it is not audited and does not mark a scenario record as
        Modified, exactly like dragging a system box. Routing a diagram tidily
        should not make a plan look like it changed.
        """
        SCENARIO_AWARE = True

        def put(self, fid):
            data = self.get_json_body()
            conn = get_db()
            try:
                self.scenario_access(conn, write=True)
                where, params = self.scope_sql()
                row = conn.execute(
                    f"SELECT flow_id FROM data_flow WHERE flow_id=? AND {where}",
                    (fid,) + params).fetchone()
                if not row:
                    return self.fail(404, "Not found")

                fields, vals = [], []
                for key in ("source_anchor", "dest_anchor"):
                    if key in data:
                        # null or empty both mean "back to automatic"; anything
                        # else has to be a real connection point.
                        value = (data[key] or None)
                        if value is not None and not valid_anchor(value):
                            return self.fail(400, f"'{value}' is not a connection point.")
                        fields.append(f"{key}=?")
                        vals.append(value)
                if "waypoints" in data:
                    points = data["waypoints"] or []
                    if not isinstance(points, list) or len(points) > MAX_WAYPOINTS:
                        return self.fail(
                            400, f"A data flow can have at most {MAX_WAYPOINTS} bends.")
                    clean = []
                    for p in points:
                        try:
                            clean.append({"x": round(float(p["x"]), 2),
                                          "y": round(float(p["y"]), 2)})
                        except (TypeError, KeyError, ValueError):
                            return self.fail(400, "A bend point needs an x and a y.")
                    fields.append("waypoints=?")
                    vals.append(json.dumps(clean) if clean else None)

                if fields:
                    vals.append(fid)
                    conn.execute(
                        f"UPDATE data_flow SET {','.join(fields)} WHERE flow_id=?", vals)
                    conn.commit()
                out = conn.execute("SELECT source_anchor, dest_anchor, waypoints "
                                   "FROM data_flow WHERE flow_id=?", (fid,)).fetchone()
                self.write_json(row_to_dict(out))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

    # -----------------------------------------------------------------------
    # Help / knowledge base
    # -----------------------------------------------------------------------
    class HelpHandler(BaseAPIHandler):
        """The articles this reader may see.

        Everything is returned in one response and searched in the browser.
        With a handful of short articles that is faster than a request per
        keystroke, and it means search keeps working while the panel is open
        even on a slow connection.

        Articles currently come from ``help_articles.py``. If they later move
        into a table, only that module changes — this endpoint and the panel
        stay as they are.
        """
        READ_ROLE = permissions.VIEWER
        # Reading help must never be blocked by a pending password change:
        # "how do I change my password" is exactly what someone needs then.
        allow_when_password_expired = True

        def get(self):
            role = self.current_user["role"]
            screen = self.get_argument("screen", None)
            pinned, relevant, rest = help_articles.list_articles(role, screen)
            self.write_json({
                "pinned": [help_articles.public(a) for a in pinned],
                "for_this_screen": [help_articles.public(a) for a in relevant],
                # Order matters: the panel renders this list, and search
                # filters it, so pinned has to come first here too.
                "articles": [help_articles.public(a, include_body=True)
                             for a in pinned + relevant + rest],
            })

    class HelpArticleHandler(BaseAPIHandler):
        """One article, for linking straight to it."""
        READ_ROLE = permissions.VIEWER
        allow_when_password_expired = True

        def get(self, article_id):
            article = help_articles.get_article(article_id,
                                                self.current_user["role"])
            if not article:
                return self.fail(404, "That help article was not found.")
            self.write_json(help_articles.public(article, include_body=True))

    # -----------------------------------------------------------------------
    # Scenarios
    # -----------------------------------------------------------------------
    def scenario_payload(conn, row, user):
        """A scenario as the frontend needs it, including this user's rights."""
        d = dict(row)
        members = conn.execute(
            """SELECT m.user_id, m.role, u.first_name, u.last_name, u.email,
                      u.role AS system_role, u.is_active
                 FROM scenario_member m JOIN app_user u ON u.user_id = m.user_id
                WHERE m.scenario_id = ? ORDER BY m.role DESC, u.last_name""",
            (row["scenario_id"],)).fetchall()
        d["members"] = rows_to_list(members)
        acc = scenarios.access(conn, user, row["scenario_id"], include_deleted=True)
        d["my_role"] = acc.scenario_role
        d["can_write"] = acc.can_write
        d["can_manage"] = acc.can_manage
        d["frozen"] = acc.frozen
        return d

    class ScenariosHandler(BaseAPIHandler):
        """List scenarios, and create one."""
        READ_ROLE = permissions.VIEWER
        # Editors and above may create a scenario. Viewers can be invited to
        # one and can edit inside it, but cannot start one of their own.
        WRITE_ROLE = permissions.EDITOR

        def get(self):
            conn = get_db()
            try:
                rows = scenarios.visible_scenarios(conn, self.current_user)
                out = [scenario_payload(conn, r, self.current_user) for r in rows]
            finally:
                conn.close()
            self.write_json(out)

        def post(self):
            data = self.get_json_body()
            name = (data.get("name") or "").strip()
            if not name:
                return self.fail(400, "These fields are required: Name.",
                                 missing=["Name"])
            start = data.get("start_from", "blank")
            if start not in ("live", "scenario", "blank"):
                return self.fail(400, "Choose what to start this scenario from.")
            source_id = data.get("source_scenario_id")

            conn = get_db()
            try:
                if start == "scenario":
                    # You can only branch from a scenario you can already open.
                    try:
                        scenarios.access(conn, self.current_user, source_id).require_read()
                    except scenarios.ScenarioError as e:
                        return self.fail(e.status, e.message)
                now = iso(utcnow())
                row = conn.execute(
                    """INSERT INTO scenario
                         (name, description, status, owner_user_id, origin_kind,
                          origin_scenario_id, created_date, updated_date,
                          created_by_email)
                       VALUES (?,?,?,?,?,?,?,?,?) RETURNING scenario_id""",
                    (name, data.get("description"), scenarios.DRAFT,
                     self.current_user["user_id"], start,
                     source_id if start == "scenario" else None,
                     now, now, self.current_user["email"])).fetchone()
                sid = row["scenario_id"]
                conn.execute(
                    """INSERT INTO scenario_member
                         (scenario_id, user_id, role, added_date, added_by_email)
                       VALUES (?,?,?,?,?)""",
                    (sid, self.current_user["user_id"], scenarios.S_OWNER, now,
                     self.current_user["email"]))

                counts = {}
                focused = 0
                if start in ("live", "scenario"):
                    counts = scenarios.clone_into(
                        conn, sid,
                        source_scenario_id=source_id if start == "scenario" else None)
                    # Optional focus: everything is still copied, so nothing is
                    # lost and a later analysis stays honest, but anything
                    # outside the chosen systems arrives hidden.
                    focus = data.get("focus_system_ids") or []
                    if focus:
                        keep = {scenarios.scoped_id(sid, scenarios.base_id(f))
                                for f in focus}
                        others = [r["system_id"] for r in conn.execute(
                            "SELECT system_id FROM system WHERE scenario_id=?",
                            (sid,)).fetchall() if r["system_id"] not in keep]
                        hidden = scenarios.set_hidden(
                            conn, sid, systems=others, hidden=True)
                        focused = hidden["system"]

                # Carry the source scenario's people across, with the roles
                # they already had. The modal has always offered this and sent
                # `copy_members`, but nothing here ever read it, so the box
                # silently did nothing and the new scenario arrived with only
                # its owner.
                #
                # The owner is skipped rather than demoted: they are already a
                # member of the new scenario as its owner, and the source's own
                # owner becomes an ordinary editor here, because owning one
                # scenario confers nothing over another.
                # Routed through add_members rather than inserting directly, so
                # carried-over people get the same treatment as named invitees:
                # the active-account check, the role validation, a notification
                # telling them they now have access, and the duplicate guard
                # that stops someone both carried over and explicitly invited
                # from breaking the primary key.
                copied_members = 0
                if start == "scenario" and source_id and data.get("copy_members"):
                    carried = [
                        # The source's owner becomes an ordinary editor here:
                        # owning one scenario confers nothing over another, and
                        # a scenario has exactly one owner — whoever made it.
                        {"user_id": m["user_id"],
                         "role": (scenarios.S_EDITOR if m["role"] == scenarios.S_OWNER
                                  else m["role"])}
                        for m in conn.execute(
                            "SELECT user_id, role FROM scenario_member WHERE scenario_id=?",
                            (source_id,)).fetchall()]
                    copied_members = self.add_members(conn, sid, carried, now)

                # Invitees named at creation time.
                invited = self.add_members(conn, sid, data.get("invite") or [], now)

                summary = {
                    "live": "started from Current Architecture",
                    "scenario": "branched from another scenario",
                    "blank": "started from a blank page",
                }[start]
                if counts:
                    summary += (f" — copied {counts.get('system', 0)} system(s) and "
                                f"{counts.get('data_flow', 0)} data flow(s)")
                    if counts.get("task"):
                        summary += f" with {counts['task']} task(s)"
                if focused:
                    summary += f", {focused} hidden to focus the diagram"
                if copied_members:
                    summary += f", {copied_members} collaborator(s) carried over"
                # One event for the whole clone, per the bulk-operation rule.
                audit.record_event(
                    conn, self.current_user, "create", "scenario", sid,
                    summary=f"Scenario '{name}' {summary}",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                created = conn.execute("SELECT * FROM scenario WHERE scenario_id=?",
                                       (sid,)).fetchone()
                payload = scenario_payload(conn, created, self.current_user)
                payload["copied"] = counts
                payload["invited"] = invited
                payload["copied_members"] = copied_members
                payload["hidden_for_focus"] = focused
            finally:
                conn.close()
            self.set_status(201)
            self.write_json(payload)

        def add_members(self, conn, sid, invites, now):
            """Add members named at creation. Returns how many were added."""
            added = 0
            for item in invites:
                uid = item.get("user_id") if isinstance(item, dict) else item
                role = (item.get("role") if isinstance(item, dict) else None) \
                    or scenarios.S_VIEWER
                if role not in (scenarios.S_VIEWER, scenarios.S_EDITOR):
                    continue
                if not uid or uid == self.current_user["user_id"]:
                    continue
                user = conn.execute(
                    "SELECT * FROM app_user WHERE user_id=? AND is_active=1",
                    (uid,)).fetchone()
                if not user:
                    continue
                # Already a member: skip rather than insert. scenario_member is
                # keyed on (scenario_id, user_id), so a second insert for the
                # same person is a 500 rather than a no-op — reachable as soon
                # as copy_members and an explicit invite name the same person.
                if conn.execute(
                        "SELECT 1 FROM scenario_member WHERE scenario_id=? AND user_id=?",
                        (sid, uid)).fetchone():
                    continue
                conn.execute(
                    """INSERT INTO scenario_member
                         (scenario_id, user_id, role, added_date, added_by_email)
                       VALUES (?,?,?,?,?)""",
                    (sid, uid, role, now, self.current_user["email"]))
                name = conn.execute("SELECT name FROM scenario WHERE scenario_id=?",
                                    (sid,)).fetchone()["name"]
                notify_user(
                    conn, uid, "scenario_invite",
                    f"You were added to the scenario '{name}'",
                    body=f"{self.current_user['first_name']} "
                         f"{self.current_user['last_name']} added you as "
                         f"{'an editor' if role == scenarios.S_EDITOR else 'a viewer'}.",
                    entity_type="scenario", entity_id=sid,
                    from_email=self.current_user["email"])
                added += 1
            return added

    class ScenarioHandler(BaseAPIHandler):
        """Read, rename, change status, soft delete, and restore one scenario."""
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER   # authority comes from membership

        def get(self, sid):
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid)
                acc.require_read()
                self.write_json(scenario_payload(conn, acc.scenario, self.current_user))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

        def put(self, sid):
            data = self.get_json_body()
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid)
                before = dict(acc.scenario)

                # Status is the one field editable while frozen — it has to be,
                # or an Approved scenario could never be reopened.
                if "status" in data:
                    acc.require_manage()
                    status = data["status"]
                    if status not in scenarios.STATUSES:
                        return self.fail(400, f"'{status}' is not a valid status.")
                    conn.execute(
                        "UPDATE scenario SET status=?, updated_date=? WHERE scenario_id=?",
                        (status, iso(utcnow()), acc.scenario_id))

                other = {k: v for k, v in data.items() if k in ("name", "description")}
                if other:
                    acc.require_manage()
                    if "name" in other and not (other["name"] or "").strip():
                        return self.fail(400, "These fields are required: Name.",
                                         missing=["Name"])
                    sets = ", ".join(f"{k}=?" for k in other)
                    conn.execute(
                        f"UPDATE scenario SET {sets}, updated_date=? WHERE scenario_id=?",
                        tuple(other.values()) + (iso(utcnow()), acc.scenario_id))

                after = conn.execute("SELECT * FROM scenario WHERE scenario_id=?",
                                     (acc.scenario_id,)).fetchone()
                # Lifecycle events belong to the live log, so an administrator
                # can see what is being planned without opening each scenario.
                audit.record_update(
                    conn, self.current_user, "scenario", acc.scenario_id,
                    before, dict(after),
                    fields=["name", "description", "status"],
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                self.write_json(scenario_payload(conn, after, self.current_user))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

        def delete(self, sid):
            """Soft delete. The plan is recoverable; nothing is actually dropped."""
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid)
                acc.require_manage()
                conn.execute(
                    "UPDATE scenario SET deleted_at=?, deleted_by_email=? "
                    "WHERE scenario_id=?",
                    (iso(utcnow()), self.current_user["email"], acc.scenario_id))
                audit.record_event(
                    conn, self.current_user, "delete", "scenario", acc.scenario_id,
                    summary=f"Scenario '{acc.scenario['name']}' deleted "
                            f"(recoverable by a SuperAdmin)",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                for m in conn.execute(
                        "SELECT user_id FROM scenario_member WHERE scenario_id=?",
                        (acc.scenario_id,)).fetchall():
                    if m["user_id"] != self.current_user["user_id"]:
                        notify_user(
                            conn, m["user_id"], "scenario_deleted",
                            f"The scenario '{acc.scenario['name']}' was deleted",
                            entity_type="scenario", entity_id=acc.scenario_id,
                            from_email=self.current_user["email"])
                conn.commit()
                self.write_json({"deleted": acc.scenario_id})
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

    class ScenarioRestoreHandler(BaseAPIHandler):
        """Bring back a soft-deleted scenario. SuperAdmin, or its owner."""
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER

        def post(self, sid):
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid,
                                       include_deleted=True)
                acc.require_manage()
                conn.execute(
                    "UPDATE scenario SET deleted_at=NULL, deleted_by_email=NULL "
                    "WHERE scenario_id=?", (acc.scenario_id,))
                audit.record_event(
                    conn, self.current_user, "restore", "scenario", acc.scenario_id,
                    summary=f"Scenario '{acc.scenario['name']}' restored",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                row = conn.execute("SELECT * FROM scenario WHERE scenario_id=?",
                                   (acc.scenario_id,)).fetchone()
                self.write_json(scenario_payload(conn, row, self.current_user))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

    class ScenarioMembersHandler(BaseAPIHandler):
        """Invite someone, change their scenario role, or remove them."""
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER

        def post(self, sid):
            data = self.get_json_body()
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid)
                acc.require_manage()
                uid = data.get("user_id")
                role = data.get("role", scenarios.S_VIEWER)
                # An owner cannot hand out ownership, and there is no such thing
                # as a scenario administrator: system Administrators and
                # SuperAdmins already have their access from their system role.
                if role not in (scenarios.S_VIEWER, scenarios.S_EDITOR):
                    return self.fail(
                        400, "A scenario member can be an editor or a viewer.")
                user = conn.execute(
                    "SELECT * FROM app_user WHERE user_id=? AND is_active=1",
                    (uid,)).fetchone()
                if not user:
                    return self.fail(404, "That user was not found, or is inactive.")
                if uid == acc.scenario["owner_user_id"]:
                    return self.fail(400, "The owner's role cannot be changed here.")

                existing = scenarios.member_role(conn, acc.scenario_id, uid)
                now = iso(utcnow())
                if existing:
                    conn.execute(
                        "UPDATE scenario_member SET role=? "
                        "WHERE scenario_id=? AND user_id=?",
                        (role, acc.scenario_id, uid))
                    action = "updated"
                else:
                    conn.execute(
                        """INSERT INTO scenario_member
                             (scenario_id, user_id, role, added_date, added_by_email)
                           VALUES (?,?,?,?,?)""",
                        (acc.scenario_id, uid, role, now, self.current_user["email"]))
                    action = "added"
                    notify_user(
                        conn, uid, "scenario_invite",
                        f"You were added to the scenario '{acc.scenario['name']}'",
                        body=f"You can "
                             f"{'edit' if role == scenarios.S_EDITOR else 'view'} "
                             f"this scenario.",
                        entity_type="scenario", entity_id=acc.scenario_id,
                        from_email=self.current_user["email"])
                audit.record_event(
                    conn, self.current_user, "update", "scenario", acc.scenario_id,
                    summary=f"{user['email']} {action} as {role} on scenario "
                            f"'{acc.scenario['name']}'",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                row = conn.execute("SELECT * FROM scenario WHERE scenario_id=?",
                                   (acc.scenario_id,)).fetchone()
                self.write_json(scenario_payload(conn, row, self.current_user))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

        def delete(self, sid, uid):
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid)
                acc.require_manage()
                uid = int(uid)
                if uid == acc.scenario["owner_user_id"]:
                    return self.fail(
                        400, "The owner cannot be removed. Transfer ownership first.")
                conn.execute(
                    "DELETE FROM scenario_member WHERE scenario_id=? AND user_id=?",
                    (acc.scenario_id, uid))
                # Their contributions stay: the audit trail is append-only, and
                # the work belongs to the scenario rather than to the person.
                audit.record_event(
                    conn, self.current_user, "update", "scenario", acc.scenario_id,
                    summary=f"User {uid} removed from scenario "
                            f"'{acc.scenario['name']}'",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                notify_user(
                    conn, uid, "scenario_removed",
                    f"You no longer have access to '{acc.scenario['name']}'",
                    entity_type="scenario", entity_id=acc.scenario_id,
                    from_email=self.current_user["email"])
                conn.commit()
                row = conn.execute("SELECT * FROM scenario WHERE scenario_id=?",
                                   (acc.scenario_id,)).fetchone()
                self.write_json(scenario_payload(conn, row, self.current_user))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

    class ScenarioVisibilityHandler(BaseAPIHandler):
        """Hide or show records in bulk, so a scenario can be focused.

        Built for multi-select: the client sends every identifier at once and
        gets one summary audit event, rather than one event per row. Hiding is
        a view preference — nothing is deleted, and nothing is excluded from
        any future analysis.
        """
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER   # scenario membership decides

        def post(self, sid):
            data = self.get_json_body()
            hide = bool(data.get("hidden", True))
            systems = data.get("system_ids") or []
            flows = data.get("flow_ids") or []
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid)
                acc.require_write()
                if not systems and not flows and not data.get("all"):
                    return self.fail(400, "Nothing was selected.")

                # "Show everything again" — the un-hide-all toggle.
                if data.get("all"):
                    systems = [r["system_id"] for r in conn.execute(
                        "SELECT system_id FROM system WHERE scenario_id=?",
                        (acc.scenario_id,)).fetchall()]
                    flows = [r["flow_id"] for r in conn.execute(
                        "SELECT flow_id FROM data_flow WHERE scenario_id=?",
                        (acc.scenario_id,)).fetchall()]

                changed = scenarios.set_hidden(
                    conn, acc.scenario_id, systems=systems, flows=flows, hidden=hide)
                verb = "hid" if hide else "showed"
                audit.record_event(
                    conn, self.current_user, "update", "scenario", acc.scenario_id,
                    summary=f"{verb.capitalize()} {changed['system']} system(s) and "
                            f"{changed['data_flow']} data flow(s) in scenario "
                            f"'{acc.scenario['name']}'",
                    scenario_id=acc.scenario_id,
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                conn.commit()
                self.write_json({"changed": changed, "hidden": hide})
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

    class ScenarioOwnerHandler(BaseAPIHandler):
        """Transfer ownership. SuperAdmin only, per the agreed rules."""
        READ_ROLE = permissions.SUPERADMIN
        WRITE_ROLE = permissions.SUPERADMIN

        def post(self, sid):
            data = self.get_json_body()
            conn = get_db()
            try:
                acc = scenarios.access(conn, self.current_user, sid,
                                       include_deleted=True)
                uid = data.get("user_id")
                user = conn.execute(
                    "SELECT * FROM app_user WHERE user_id=? AND is_active=1",
                    (uid,)).fetchone()
                if not user:
                    return self.fail(404, "That user was not found, or is inactive.")
                now = iso(utcnow())
                old_owner = acc.scenario["owner_user_id"]
                conn.execute(
                    "UPDATE scenario SET owner_user_id=?, updated_date=? "
                    "WHERE scenario_id=?", (uid, now, acc.scenario_id))
                # The previous owner stays on as an editor rather than losing
                # access to work they did.
                conn.execute(
                    "UPDATE scenario_member SET role=? WHERE scenario_id=? AND user_id=?",
                    (scenarios.S_EDITOR, acc.scenario_id, old_owner))
                if scenarios.member_role(conn, acc.scenario_id, uid):
                    conn.execute(
                        "UPDATE scenario_member SET role=? "
                        "WHERE scenario_id=? AND user_id=?",
                        (scenarios.S_OWNER, acc.scenario_id, uid))
                else:
                    conn.execute(
                        """INSERT INTO scenario_member
                             (scenario_id, user_id, role, added_date, added_by_email)
                           VALUES (?,?,?,?,?)""",
                        (acc.scenario_id, uid, scenarios.S_OWNER, now,
                         self.current_user["email"]))
                audit.record_event(
                    conn, self.current_user, "update", "scenario", acc.scenario_id,
                    summary=f"Ownership of '{acc.scenario['name']}' transferred to "
                            f"{user['email']}",
                    ip_address=self.client_ip(), user_agent=self.user_agent())
                notify_user(
                    conn, uid, "scenario_owner",
                    f"You now own the scenario '{acc.scenario['name']}'",
                    entity_type="scenario", entity_id=acc.scenario_id,
                    from_email=self.current_user["email"])
                conn.commit()
                row = conn.execute("SELECT * FROM scenario WHERE scenario_id=?",
                                   (acc.scenario_id,)).fetchone()
                self.write_json(scenario_payload(conn, row, self.current_user))
            except scenarios.ScenarioError as e:
                return self.fail(e.status, e.message)
            finally:
                conn.close()

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------
    class NotificationsHandler(BaseAPIHandler):
        READ_ROLE = permissions.VIEWER
        WRITE_ROLE = permissions.VIEWER
        allow_when_password_expired = True

        def get(self):
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM notification WHERE user_id=? AND seen_at IS NULL "
                "ORDER BY created_at ASC, notification_id ASC",
                (self.current_user["user_id"],)).fetchall()
            conn.close()
            out = []
            for r in rows_to_list(rows):
                if r.get("detail_json"):
                    try:
                        r["detail"] = json.loads(r["detail_json"])
                    except ValueError:
                        r["detail"] = None
                    r.pop("detail_json", None)
                out.append(r)
            self.write_json({"unseen": out})

        def post(self):
            """Mark everything currently unseen as seen ("Got it")."""
            conn = get_db()
            conn.execute("UPDATE notification SET seen_at=? "
                         "WHERE user_id=? AND seen_at IS NULL",
                         (iso(utcnow()), self.current_user["user_id"]))
            conn.commit()
            conn.close()
            self.write_json({"ok": True})

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

    class AppPageHandler(AppStaticFileHandler):
        """Serve `index.html` for a route that is a *page*, not a file.

        `/reset-password` was `(r"/(reset-password)", AppStaticFileHandler,
        {"default_filename": "index.html"})`, which looks right and **404s**:
        the capture group becomes the path `StaticFileHandler` is asked for, so
        it went looking for `static/reset-password`, and `default_filename`
        only applies to a **directory**. Neither a file nor a directory, so
        nothing to serve.

        That is why it has to be a handler rather than a cleverer route: the
        file to serve is a constant, and the path in the URL is not a filename
        at all. `set_extra_headers` still sees `index.html`, so the no-cache
        rule above continues to apply.

        Nothing automated found this. `t_email.py` exercises
        `POST /api/auth/reset-password`, which is a different thing with a
        similar name, and no check in this project loads a page. It took
        somebody following a real link from a real message — which is now a
        smoke-test item, for exactly this reason.
        """
        async def get(self, path=None, include_body=True):
            await super().get("index.html", include_body)

    def make_app():
        return tornado.web.Application([
            # Public
            (r"/api/version", VersionHandler),
            # Authentication
            (r"/api/auth/login", AuthLoginHandler),
            (r"/api/auth/logout", AuthLogoutHandler),
            (r"/api/auth/me", AuthMeHandler),
            (r"/api/auth/profile", ProfileHandler),
            (r"/api/users/pickable", PickableUsersHandler),
            (r"/api/auth/change-password", ChangePasswordHandler),
            (r"/api/auth/forgot-password", ForgotPasswordHandler),
            (r"/api/auth/reset-password", ResetPasswordHandler),
            (r"/api/email/test", EmailTestHandler),
            # Administration
            (r"/api/users", UsersHandler),
            (r"/api/users/([^/]+)", UserHandler),
            (r"/api/sessions", UserSessionsHandler),
            (r"/api/sessions/([^/]+)", UserSessionsHandler),
            (r"/api/preferences", PreferencesHandler),
            (r"/api/system-types", SystemTypesHandler),
            (r"/api/system-types/([^/]+)", SystemTypeHandler),
            (r"/api/task-types", TaskTypesHandler),
            (r"/api/task-types/([^/]+)", TaskTypeHandler),
            # Tasks. The copy route sits above the bare id so it cannot be
            # shadowed by a task literally called "copy".
            (r"/api/tasks", TasksHandler),
            (r"/api/tasks/([^/]+)/copy", TaskCopyHandler),
            (r"/api/tasks/([^/]+)/position", TaskPositionHandler),
            (r"/api/tasks/([^/]+)", TaskHandler),
            # Scenarios. The member route is listed before the single-scenario
            # route so "/api/scenarios/3/members/7" is not swallowed by it.
            (r"/api/help", HelpHandler),
            (r"/api/help/([a-z0-9-]+)", HelpArticleHandler),
            (r"/api/scenarios", ScenariosHandler),
            (r"/api/scenarios/([^/]+)/members/([^/]+)", ScenarioMembersHandler),
            (r"/api/scenarios/([^/]+)/members", ScenarioMembersHandler),
            (r"/api/scenarios/([^/]+)/restore", ScenarioRestoreHandler),
            (r"/api/scenarios/([^/]+)/visibility", ScenarioVisibilityHandler),
            (r"/api/scenarios/([^/]+)/owner", ScenarioOwnerHandler),
            (r"/api/scenarios/([^/]+)", ScenarioHandler),
            (r"/api/notifications", NotificationsHandler),
            (r"/api/audit/export", AuditExportHandler),
            (r"/api/audit/([0-9]+)/revert", AuditRevertHandler),
            (r"/api/audit", AuditHandler),
            # Release notes
            (r"/api/field-definitions", FieldDefinitionsHandler),
            (r"/api/field-catalog", FieldCatalogHandler),
            (r"/api/field-visibility", FieldVisibilityHandler),
            (r"/api/branding", BrandingHandler),
            (r"/api/branding/logo", BrandingLogoHandler),
            (r"/api/branding/public", PublicBrandingHandler),
            (r"/api/releases", ReleasesHandler),
            (r"/api/releases/([^/]+)", ReleaseHandler),
            (r"/api/whats-new", WhatsNewHandler),
            # Catalog
            (r"/api/systems", SystemsHandler),
            (r"/api/systems/([^/]+)/schema/upload", SchemaUploadHandler),
            (r"/api/systems/([^/]+)/schema/snapshots", SchemaSnapshotsHandler),
            (r"/api/systems/([^/]+)/schema", SystemSchemaHandler),
            (r"/api/systems/([^/]+)/position", PositionHandler),
            (r"/api/flows/([^/]+)/route", FlowRouteHandler),
            (r"/api/systems/([^/]+)", SystemHandler),
            (r"/api/schema/tables/([^/]+)/position", SchemaTablePositionHandler),
            (r"/api/schema/tables/([^/]+)", SchemaTableHandler),
            (r"/api/schema/columns/([^/]+)", SchemaColumnHandler),
            (r"/api/flows", FlowsHandler),
            (r"/api/flows/([^/]+)", FlowHandler),
            (r"/api/elements", ElementsHandler),
            (r"/api/elements/([^/]+)", ElementHandler),
            (r"/api/schema/snapshots/([0-9]+)/revert", SchemaRevertHandler),
            # Resources (documents and links)
            # More specific routes first, so they cannot be shadowed.
            (r"/api/resources/(system|data_flow)/([^/]+)/field-list", FieldListHandler),
            (r"/api/resources/(system|data_flow|task)/([^/]+)", ResourcesHandler),
            (r"/api/resources/([0-9]+)/clear", ResourceClearHandler),
            (r"/api/resources/([0-9]+)/file", ResourceFileHandler),
            (r"/api/resources/([0-9]+)/versions", ResourceVersionsHandler),
            (r"/api/resources/([0-9]+)", ResourceHandler),
            (r"/api/contributors", ContributorsHandler),
            (r"/api/(systems|flows)/([^/]+)/delete-impact", DeleteImpactHandler),
            (r"/api/contributors/([^/]+)/references", ContributorReferencesHandler),
            (r"/api/contributors/([^/]+)", ContributorHandler),
            (r"/api/agreements", AgreementsHandler),
            # The one non-root page in the app. A reset link has to point at a
            # path rather than at "/" so that the token is not sitting in the
            # address bar of the ordinary diagram, and `AppStaticFileHandler`
            # would 404 it: there is no reset-password.html, the whole app is
            # one index.html. So this serves that index and the browser decides
            # what to draw from the path — the SPA arrangement, used exactly
            # once rather than generally, because a catch-all that returned the
            # app for every unknown path would turn a mistyped API route into a
            # page of HTML instead of an error.
            #
            # `AppPageHandler`, not `AppStaticFileHandler` with a
            # `default_filename` — see that class for why the obvious form
            # 404s. No capture group: there is no filename in this URL.
            (r"/reset-password", AppPageHandler, {
                "path": str(STATIC_DIR),
            }),
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
    ensure_releases()
    ensure_field_definitions()
    ensure_task_types()
    bootstrap_superadmin()

    port = int(os.environ.get("PORT", 8765))

    # The test sink swallows every outgoing message. It exists so a test suite
    # can never mail a real person, and it is a hole if it is ever set on a
    # real deployment: password reset would report success and deliver nothing.
    #
    # **Refusing to start is the whole protection.** The previous version only
    # warned, and a warning at startup is read once by whoever deploys and
    # never again — an environment file carried over from a test deployment
    # would take the sink with it, and the symptom arrives weeks later as users
    # who never receive a reset, with every setting an administrator can see
    # looking correct. Exiting here costs a confused minute to someone who set
    # one variable and meant to set two; the alternative costs a password reset
    # that lies. See the note above `SINK_ENV` in mailer.py.
    refusal = mailer.sink_refusal()
    if refusal:
        print(f"\n  *** REFUSING TO START ***\n  {refusal}\n")
        raise SystemExit(2)

    if mailer.sink_path():
        print(f"\n  *** {mailer.SINK_ENV} is set. NO EMAIL WILL BE SENT. ***"
              f"\n  *** Messages are being written to {mailer.sink_path()}"
              f"\n  *** Unset it unless this is a test environment.\n")

    app = make_app()
    app.listen(port)
    print(f"\n  Data Lineage Tracker running at http://localhost:{port}\n")
    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        close_pool()
