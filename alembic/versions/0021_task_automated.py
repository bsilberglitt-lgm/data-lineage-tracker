"""Mark a task as automated.

Ben: "this will allow a user to see in the diagram which tasks are automated
vs manual." The diagram already says what the work *is* and where it stands;
this says whether a person has to do it. Looking at a flow and seeing which of
its tasks need a human is the question behind "what happens if she is on
leave".

**A column on `task`, not a task type.** Types are a controlled list a
SuperAdmin owns — Validation, Manual Transfer and so on — and automation cuts
across all of them: a validation can be automated or run by hand, and so can a
transfer. Folding it into the type would double the list and make the
catalog's Type column answer two questions badly.

**INTEGER 0/1, not BOOLEAN**, per the project convention. The API and the
frontend both depend on it.

**Defaults to 0.** Every task that exists today was entered by somebody
describing work a person does, and silently relabelling that history as
automated would be a claim nobody made. An unticked box is the honest state
for a question that has not been asked yet.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "task",
        sa.Column("is_automated", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
    )


def downgrade():
    op.drop_column("task", "is_automated")
