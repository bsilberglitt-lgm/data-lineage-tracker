"""Fields an organisation does not use, hidden from both surfaces.

The app offers eighty fields per record because it was built to cover what any
state education agency might need to record. No single department uses all of
them, and an unused field is not free: it is one more box to scroll past, one
more thing that looks unanswered, and one more place where a reader cannot tell
"we do not track this" from "nobody has filled this in yet".

A row here means that field is not drawn for that record type — not in the
details panel and not in the Add window.

**The stored value is never touched.** Hiding is a display preference, so a
column that already holds data keeps it, and un-hiding brings it back intact.
That is deliberate: the alternative is a preference that destroys data, and a
mis-click would then be unrecoverable without a restore.

**Per record type only — no shared "*" scope.** Field *definitions* support a
shared row (`entity_type = "*"`) because the meaning of Data Steward really is
the same wherever it appears. Whether a field is *used* is a different
question, answered per record type: a department might record Data Standards on
data flows and never on systems. Allowing "*" here would also mean every
hiding check had to resolve a two-level override, and a field vanishing from
three screens because of one tick is a worse surprise than three ticks.

**Required fields can never be hidden.** The Add window cannot be submitted
without them, so hiding one would make the record impossible to create. The
catalogue records which fields are required and the API refuses those; the
frontend declines to draw the toggle as well.

No foreign key, for the same reason as `field_definition`: these describe the
schema rather than any record, and must outlive every system being deleted.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "field_hidden",
        sa.Column("entity_type", sa.Text(), primary_key=True),
        sa.Column("column_name", sa.Text(), primary_key=True),
        # Boolean-ish columns are INTEGER 0/1 by this project's convention.
        # A row could have carried the meaning on its own, but an explicit
        # column lets "shown, decided deliberately" be told apart from "never
        # considered", which the audit trail reads better for.
        sa.Column("hidden", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_date", sa.Text()),
        sa.Column("updated_by", sa.Integer()),
    )


def downgrade():
    op.drop_table("field_hidden")
