"""An optional short label for a data flow, shown on the diagram.

The diagram labels each arrow with its identifier (``PL-003``), which is
unambiguous but says nothing. ``display_name`` lets a flow carry a short human
label — "Nightly enrolment" — that is shown instead when it is set.

Deliberately separate from ``name``: the full name is often a sentence, far too
long to sit on an arrow, and shortening it would lose information the catalog
needs. Capped at 20 characters in the application, which is about what fits on
an arrow without covering the diagram.

Unlike the connector columns in 0007 this **is** content, not layout: it is
part of how the flow is described, so it is audited and marks a scenario record
as Modified like any other field.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("data_flow", sa.Column("display_name", sa.Text()))


def downgrade():
    op.drop_column("data_flow", "display_name")
