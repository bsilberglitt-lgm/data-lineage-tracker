"""Connection points and bend points for data flow arrows.

Until now an arrow was drawn center-to-center between two systems, which meant
two flows between the same pair sat exactly on top of each other — unreadable
for the common case of a bidirectional pair recorded as two flows.

Three columns, all optional:

``source_anchor`` / ``dest_anchor``
    Which point on the box the arrow attaches to, as ``side:index`` — for
    example ``top:1`` or ``left:0``. Three points per side, twelve in total.
    NULL means *automatic*: the app picks the sensible pair based on where the
    two boxes are, which is what every existing flow does today and continues
    to do until someone drags an end.

``waypoints``
    A JSON list of ``{"x": …, "y": …}`` in diagram coordinates, for routing an
    arrow around something in the way. NULL or ``[]`` is a straight line.

**These are layout, not content.** Like ``x_pos``/``y_pos`` on a system, they
describe how the picture is arranged rather than what the data flow *is*. So
they are excluded from the audit trail and do not mark a scenario record as
Modified — otherwise tidying a diagram would fill the history with noise and
make a plan look changed when nothing about it had.

Anchors are stored as text rather than coordinates deliberately: an anchor
follows its box when the box moves, and survives a change to the box's size.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("data_flow", sa.Column("source_anchor", sa.Text()))
    op.add_column("data_flow", sa.Column("dest_anchor", sa.Text()))
    op.add_column("data_flow", sa.Column("waypoints", sa.Text()))


def downgrade():
    op.drop_column("data_flow", "waypoints")
    op.drop_column("data_flow", "dest_anchor")
    op.drop_column("data_flow", "source_anchor")
