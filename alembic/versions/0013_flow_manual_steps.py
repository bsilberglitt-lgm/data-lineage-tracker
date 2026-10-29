"""Whether a data flow has a human step in it.

A flow can be fully automated or can depend on someone doing something —
reviewing and approving data before it moves, uploading a file to a location by
hand, or any other step nobody has automated. That distinction matters for
governance: a manual step is where timing slips, where a person sees data they
may not need to see, and where the flow stops if that person is away.

**Not the same as the Manual Entry system type.** Manual Entry describes data
*entered directly into a system*, by filling in a screen or uploading a
spreadsheet the user prepared. This is about a *flow* between systems that has
human intervention somewhere along it. The two are easy to confuse, so the
field ships with a definition — see `SEED_FIELD_DEFINITIONS` in app.py, which
is where the wording lives rather than here, so an administrator can reword it
without a migration.

INTEGER 0/1 rather than BOOLEAN, per the convention in CLAUDE.md. Default 0:
an existing flow is not claimed to have manual steps just because nobody has
said yet, and 0 against NULL means the frontend never has to tell "no" from
"unanswered" for a column that has only ever had two meanings.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "data_flow",
        sa.Column("manual_steps", sa.Integer(), nullable=False,
                  server_default="0"))


def downgrade():
    op.drop_column("data_flow", "manual_steps")
