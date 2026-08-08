"""Audit trail: who changed what, when, and from where.

Two tables:

* ``audit_event``       — one row per action (create / update / delete / login /
                          login_failed / permission_denied / backfill).
* ``audit_field_change``— one row per field changed by an update, with the old
                          and new values.

The actor's email is copied onto every event rather than only referenced by id,
so the history stays readable even if an account is later renamed. Audit rows
are append-only: nothing in the application ever updates or deletes them.
"""

import datetime
import json

# Action names
CREATE = "create"
UPDATE = "update"
DELETE = "delete"
LOGIN = "login"
LOGOUT = "logout"
LOGIN_FAILED = "login_failed"
PERMISSION_DENIED = "permission_denied"
PASSWORD_CHANGED = "password_changed"
BACKFILL = "backfill"

# Values that should never be written into the audit log, even though they
# appear on the records being audited.
SENSITIVE_FIELDS = {"password_hash", "password", "csrf_token", "token_hash"}

# Fields that change on every write and would otherwise bury real changes.
IGNORED_FIELDS = {"updated_date", "created_date", "updated_by", "created_by"}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _stringify(value):
    """Render a value for storage, keeping it readable in the Audit screen."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def diff_records(before, after, fields=None):
    """Return {field: (old, new)} for values that actually changed.

    ``fields`` limits the comparison to a known set of columns; without it every
    key present in ``after`` is compared.
    """
    before = before or {}
    after = after or {}
    keys = fields if fields is not None else after.keys()
    changes = {}
    for key in keys:
        if key in IGNORED_FIELDS or key in SENSITIVE_FIELDS:
            continue
        if key not in after:
            continue
        old = before.get(key)
        new = after.get(key)
        # Compare as strings so 1 vs "1" and 0.0 vs 0 are not spurious changes.
        if _stringify(old) != _stringify(new):
            changes[key] = (old, new)
    return changes


def record_event(conn, actor, action, entity_type=None, entity_id=None,
                 changes=None, summary=None, ip_address=None, user_agent=None):
    """Write one audit event plus any field-level changes.

    ``actor`` may be a user row/dict, or None for anonymous actions such as a
    failed login. Returns the new event id.
    """
    actor = actor or {}
    row = conn.execute(
        """INSERT INTO audit_event
             (occurred_at, actor_user_id, actor_email, action, entity_type,
              entity_id, summary, ip_address, user_agent)
           VALUES (?,?,?,?,?,?,?,?,?)
           RETURNING event_id""",
        (_now(), actor.get("user_id"), actor.get("email"), action, entity_type,
         str(entity_id) if entity_id is not None else None,
         summary, ip_address, user_agent),
    ).fetchone()
    event_id = row["event_id"]

    for field, (old, new) in (changes or {}).items():
        conn.execute(
            """INSERT INTO audit_field_change
                 (event_id, field_name, old_value, new_value)
               VALUES (?,?,?,?)""",
            (event_id, field, _stringify(old), _stringify(new)),
        )
    return event_id


def record_create(conn, actor, entity_type, entity_id, values, **kw):
    """Log a creation, capturing the initial values that were set."""
    changes = {k: (None, v) for k, v in (values or {}).items()
               if v not in (None, "") and k not in IGNORED_FIELDS
               and k not in SENSITIVE_FIELDS}
    return record_event(conn, actor, CREATE, entity_type, entity_id,
                        changes=changes, **kw)


def record_update(conn, actor, entity_type, entity_id, before, after,
                  fields=None, **kw):
    """Log an update. Returns None (and writes nothing) if nothing changed."""
    changes = diff_records(before, after, fields)
    if not changes:
        return None
    return record_event(conn, actor, UPDATE, entity_type, entity_id,
                        changes=changes, **kw)


def record_delete(conn, actor, entity_type, entity_id, before, **kw):
    """Log a deletion, capturing what the record looked like beforehand."""
    changes = {k: (v, None) for k, v in (before or {}).items()
               if v not in (None, "") and k not in IGNORED_FIELDS
               and k not in SENSITIVE_FIELDS}
    return record_event(conn, actor, DELETE, entity_type, entity_id,
                        changes=changes, **kw)


# ---------------------------------------------------------------------------
# Querying (Audit screen + CSV export)
# ---------------------------------------------------------------------------

def query_events(conn, start=None, end=None, actor_email=None, action=None,
                 entity_type=None, entity_id=None, limit=200, offset=0,
                 include_changes=True):
    """Return audit events matching the given filters, newest first."""
    where, params = [], []
    if start:
        where.append("e.occurred_at >= ?")
        params.append(start)
    if end:
        where.append("e.occurred_at <= ?")
        params.append(end)
    if actor_email:
        where.append("LOWER(e.actor_email) LIKE ?")
        params.append(f"%{actor_email.strip().lower()}%")
    if action:
        where.append("e.action = ?")
        params.append(action)
    if entity_type:
        where.append("e.entity_type = ?")
        params.append(entity_type)
    if entity_id:
        where.append("e.entity_id = ?")
        params.append(str(entity_id))

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM audit_event e {clause}", tuple(params)
    ).fetchone()[0]

    rows = conn.execute(
        f"""SELECT e.* FROM audit_event e {clause}
            ORDER BY e.occurred_at DESC, e.event_id DESC
            LIMIT ? OFFSET ?""",
        tuple(params) + (int(limit), int(offset)),
    ).fetchall()

    events = [dict(r) for r in rows]
    if include_changes and events:
        ids = [e["event_id"] for e in events]
        placeholders = ",".join("?" for _ in ids)
        changes = conn.execute(
            f"""SELECT * FROM audit_field_change
                WHERE event_id IN ({placeholders})
                ORDER BY change_id""",
            tuple(ids),
        ).fetchall()
        by_event = {}
        for c in changes:
            by_event.setdefault(c["event_id"], []).append(dict(c))
        for e in events:
            e["changes"] = by_event.get(e["event_id"], [])
    return events, total
