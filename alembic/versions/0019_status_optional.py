"""A status nobody has set yet is not "Not started".

0016 made `status` NOT NULL with a default of `'not_started'`, so every task and
every subtask claimed a status from the moment it existed. That is a small lie
with a visible consequence: a diagram where nothing has been assessed looks
exactly like a diagram where everything has been assessed and found not to have
begun. Ben asked for the status to start empty, and he is right — "we have not
said" and "it has not started" are different facts, and the first is the honest
default for a record somebody has only just created.

So both columns become nullable with no default. NULL means nobody has said.

Consequences worth knowing, all handled in `app.py`:

* `derived_task_status` **ignores** subtasks with no status. A task whose
  subtasks all have none has no status either, rather than reporting
  "Not started" on their behalf.
* The circle's tooltip leaves the parentheses out entirely when there is no
  status, because "Name ()" reads as a fault.
* The catalog shows a dash, which is what it shows for every other empty value.

**`op.alter_column`, which the harness schema generator had to learn.** SQLite
cannot alter a column, so `tools/gen_harness_schema.py` now collects nullability
changes in a first pass and emits the affected `CREATE TABLE` without the
NOT NULL — the harness ends up with the schema these migrations describe rather
than the schema the first one described. Before that it silently kept NOT NULL
and every insert of a null status failed in the test harness only, which is the
exact failure shape this project keeps finding.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    # Dropping the server default as well as the NOT NULL: a default of
    # 'not_started' would go on filling the column for anything that does not
    # name it, which is the behavior being removed.
    op.alter_column("task", "status", existing_type=sa.Text(),
                    nullable=True, server_default=None)
    op.alter_column("task_step", "status", existing_type=sa.Text(),
                    nullable=True, server_default=None)


def downgrade():
    # Rows with no status have to be given one before the constraint can come
    # back, and 'not_started' is the value that was there before.
    op.execute("UPDATE task SET status='not_started' WHERE status IS NULL")
    op.execute("UPDATE task_step SET status='not_started' WHERE status IS NULL")
    op.alter_column("task", "status", existing_type=sa.Text(),
                    nullable=False, server_default=sa.text("'not_started'"))
    op.alter_column("task_step", "status", existing_type=sa.Text(),
                    nullable=False, server_default=sa.text("'not_started'"))
