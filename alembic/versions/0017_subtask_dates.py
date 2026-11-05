"""Expected dates on a subtask, not just on the task.

A task carries one expected start and one expected end. That is too coarse for
what tasks are for: a validation run whose first check happens the day the file
lands and whose sign-off is a week later has two dates that matter, and a single
range over the whole task hides which part is late. So a subtask gets its own
pair, and the task's pair stays as the outer window.

Both nullable with no default. A subtask that simply happens "during the task"
carries nothing rather than a copy of its parent's dates, which would be a
snapshot that stops being true the moment the task's dates move.

**A separate migration rather than an edit to 0016**, which may already have
run on a laptop. Plain `add_column` rather than `ADD COLUMN IF NOT EXISTS`
because nothing has ever created these columns anywhere — the idempotent form
is for repairing a migration that was edited after running, which is not this.
It also matters that `op.add_column` is a form `tools/gen_harness_schema.py`
understands: raw `op.execute` DDL is not translated into the harness schema,
so the test database would silently lack the columns.

The table is still called `task_step`. The UI calls these **subtasks**, which
is Ben's word for them and the one users see; renaming the table would mean
editing 0016 after it has run. The two names describe the same thing — see
CLAUDE.md.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_step", sa.Column("expected_start", sa.Text()))
    op.add_column("task_step", sa.Column("expected_end", sa.Text()))


def downgrade():
    op.drop_column("task_step", "expected_end")
    op.drop_column("task_step", "expected_start")
