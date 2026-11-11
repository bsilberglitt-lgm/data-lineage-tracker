"""A task can wait on other tasks.

Ben: "when all a task's dependencies have a status of completed, that task
would be considered past its start date." The shape that followed from the
conversation is a plain many-to-many: a task names the tasks it waits on, with
no ordering, no lag, and no type of link.

**Deliberately only at the task level.** Subtasks are an ordered checklist
inside one piece of work, and a subtask waiting on a task in another system is
not a thing anybody asked for — it would also give the derived status two
masters. Dependencies between *tasks* are the real relationship: the load that
cannot run until the file arrives.

**No column on `task`.** A nullable `depends_on` would allow exactly one, and
Ben asked for several from the start ("a button to add another dependency").
A join table is also what makes the cycle check a graph walk rather than a
special case.

**The pair is the primary key**, so naming the same dependency twice is
impossible rather than merely discouraged. Nothing else is stored: who added
it and when are in the audit trail, which is where a task's history already
lives.

**Cascades are the application's job, as they are for `task_step` and
`task_contributor`.** Deleting a task has to clear the rows that name it in
*either* column — a dangling `depends_on_task_id` would leave a task waiting
forever on something that no longer exists, and the colour coding would read
that as "not complete" and show it blocked. `delete_tasks_for` and the single
task DELETE both do this.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now() at time zone 'utc')::text")


def upgrade():
    op.create_table(
        "task_dependency",
        # The task that waits.
        sa.Column("task_id", sa.Text(), primary_key=True),
        # The task it waits on. No foreign key, for the same reason task_id
        # carries none in 0016: these identifiers are scenario-scoped strings
        # assembled by next_task_id, and a constraint here would fight the
        # copy-on-write that gives a scenario its own TSK ids.
        sa.Column("depends_on_task_id", sa.Text(), primary_key=True),
        sa.Column("added_date", sa.Text(), nullable=False,
                  server_default=NOW_TEXT),
        sa.Column("added_by", sa.Integer()),
    )
    # Both directions are queried: "what does this task wait on" when drawing a
    # flyout, and "what waits on this task" when deleting one.
    op.create_index("ix_task_dependency_task", "task_dependency", ["task_id"])
    op.create_index("ix_task_dependency_on", "task_dependency",
                    ["depends_on_task_id"])


def downgrade():
    op.drop_index("ix_task_dependency_on", table_name="task_dependency")
    op.drop_index("ix_task_dependency_task", table_name="task_dependency")
    op.drop_table("task_dependency")
