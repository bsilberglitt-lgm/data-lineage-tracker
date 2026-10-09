"""Remove the "Deprecated" status by converting the records that use it.

"Deprecated" and "Retired" were two words for the same idea, and scenarios now
express retirement properly through their own change state — an item retired by
a plan is drawn faded and struck through. Keeping a near-synonym in the Status
dropdown invited people to use the wrong one.

**Converted to "Under Review", not to "Retired".** Status describes a system's
real-world lifecycle and has no "Retired" value; retiring is something a
*scenario* does, not a status a live record carries. "Under Review" is the
closest surviving value, and it was chosen deliberately in the knowledge that
it does not mean quite the same thing.

**Every conversion is written to the audit log first**, with the original value
in the `before` column, so a record that turns out to have been mislabelled can
be identified and set back by hand. The audit log is append-only, so this is
the one durable record of what each value used to be.

In practice this converted **nothing**: production had zero Deprecated systems
and zero Deprecated data flows when it was written, and the seed database has
none either. It exists for other environments, for anything set between the
deploy and this migration running, and so the change is recorded rather than
done invisibly.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09

"""
import datetime

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

OLD, NEW = "Deprecated", "Under Review"


def upgrade():
    conn = op.get_bind()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for table, pk in [("system", "system_id"), ("data_flow", "flow_id")]:
        rows = conn.execute(
            sa.text(f"SELECT {pk} AS pk FROM {table} WHERE status = :old"),
            {"old": OLD}).fetchall()
        if not rows:
            continue

        # One audit event per table, per the bulk-operation rule in CLAUDE.md:
        # a row each would bury real activity. The per-record field changes
        # below carry the detail.
        event = conn.execute(
            sa.text("""INSERT INTO audit_event
                         (occurred_at, actor_user_id, actor_email, action,
                          entity_type, entity_id, summary)
                       VALUES (:at, NULL, 'system@migration', 'update',
                               :etype, NULL, :summary)
                       RETURNING event_id"""),
            {"at": now, "etype": table,
             "summary": f"Migration 0010: {len(rows)} {table} record(s) changed "
                        f"from '{OLD}' to '{NEW}'; the Deprecated status was "
                        f"removed. Original values are in this event's field "
                        f"changes."}).fetchone()

        for r in rows:
            conn.execute(
                sa.text("""INSERT INTO audit_field_change
                             (event_id, field_name, old_value, new_value)
                           VALUES (:eid, :field, :old, :new)"""),
                {"eid": event[0], "field": f"{r[0]}.status", "old": OLD, "new": NEW})

        conn.execute(
            sa.text(f"UPDATE {table} SET status = :new WHERE status = :old"),
            {"new": NEW, "old": OLD})


def downgrade():
    # Deliberately not reversed. Which records were "Deprecated" before is
    # recorded in the audit log, but turning every "Under Review" record back
    # would also catch records that were always Under Review. Restoring the
    # distinction means reading the audit event, which is a manual job.
    pass
