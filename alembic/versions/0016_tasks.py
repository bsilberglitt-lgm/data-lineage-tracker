"""Tasks: the work that keeps a data flow or a data store running.

A data lineage diagram says what connects to what. It has never said who has to
*do* something for that connection to work — the validation steps before a load
is accepted, the stored procedure somebody runs, the file a person downloads and
uploads by hand. Those are where a pipeline actually fails, and until now they
lived in people's heads and in spreadsheets.

A task attaches to exactly **one** flow or **one** system, draws as a small
circle on it, and carries who is responsible, when it is expected, what state it
is in, and — for the ones that are really a checklist — its individual steps.

Four decisions here are worth knowing before changing anything.

**One expected start and one expected end. No recurrence.** The first sketch had
several date pairs per task with a flag marking the active one. That flag would
go stale and the status light would then quietly lie, which is worse than no
light at all, because people stop trusting every other one. A quarterly job is
instead four tasks, each with its own dates and its own status, created with the
copy action — so the March run's outcome stays visible in June. That removed an
entire occurrences table and all the active-pair arithmetic with it.

**Scenario-scoped, like every other record.** `scenario_id`, `origin_id` and
`change_state`, and copied wholesale when a scenario is created — not
copy-on-write. Tasks are the first record type whose *parent* is another record,
so the copy has to run **after** systems and flows and remap `anchor_id` through
the same `id_map` that already repoints flow endpoints and element links. Copy
them earlier and every task in the scenario points at a live parent, which is
precisely the cross-view reference the leak tests exist to catch.

**Position means different things depending on what it is attached to**, which
is why the two columns are named for their role rather than for an axis:

- On a **flow**: `pos_main` is a fraction from 0 to 1 along the path and
  `pos_cross` is a perpendicular offset in diagram units. Storing absolute
  coordinates would strand every circle in open space the first time somebody
  dragged a system or rerouted an arrow.
- On a **system**: both are offsets in diagram units from the box's own origin,
  so the circle travels with the box.

**A step's status belongs to the step.** The task's overall status is derived
from them where steps exist, so a task cannot read Completed with three steps
unfinished underneath. Only a task with no steps carries a status set by hand.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now())::text")

# **The starting set of task types is NOT seeded here.** It lives in
# `SEED_TASK_TYPES` in app.py and is inserted by `ensure_task_types()` when the
# table is empty.
#
# System types went the other way — seeded in migration 0004 — and the result
# is the list written twice, once there and once by hand in
# tools/harness_setup.py, because the harness builds its schema from these
# files and ignores anything that is not DDL. Two copies of a list is the
# failure this project keeps finding. Seeding from the application means one
# copy, and it fills production and the test harness by the same path.
#
# Only when the table is empty, so a type an administrator deletes stays
# deleted instead of returning at the next restart.


def upgrade():
    # ---- Task types: editable data, like system types --------------------
    op.create_table(
        "task_type",
        sa.Column("type_name", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default=sa.text("100")),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_by", sa.Integer()),
    )

    # ---- The task itself -------------------------------------------------
    op.create_table(
        "task",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        # Not a foreign key to task_type: renaming a type should not have to
        # rewrite every task, and system_type is referenced the same loose way.
        sa.Column("task_type", sa.Text()),
        sa.Column("description", sa.Text()),

        # Exactly one parent. 'system' or 'data_flow'; the id is resolved
        # against whichever table that names. Two nullable foreign keys were
        # the alternative and would have allowed a task attached to both, or
        # to neither.
        sa.Column("anchor_kind", sa.Text(), nullable=False),
        sa.Column("anchor_id", sa.Text(), nullable=False),
        # See the module docstring: the meaning depends on anchor_kind.
        sa.Column("pos_main", sa.Float()),
        sa.Column("pos_cross", sa.Float()),

        sa.Column("expected_start", sa.Text()),
        sa.Column("expected_end", sa.Text()),
        # 'not_started' | 'in_progress' | 'completed'. Ignored, and recomputed,
        # for any task that has steps.
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'not_started'")),
        sa.Column("completed_date", sa.Text()),
        sa.Column("notes", sa.Text()),

        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),

        # Scenario scoping, matching system and data_flow exactly.
        sa.Column("scenario_id", sa.Integer(),
                  sa.ForeignKey("scenario.scenario_id")),
        sa.Column("origin_id", sa.Text()),
        sa.Column("change_state", sa.Text(), nullable=False,
                  server_default=sa.text("'unchanged'")),
    )
    op.create_index("ix_task_anchor", "task", ["anchor_kind", "anchor_id"])
    op.create_index("ix_task_scenario", "task", ["scenario_id"])

    # ---- Steps: an ordered checklist, each with its own assignee ---------
    op.create_table(
        "task_step",
        sa.Column("step_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default=sa.text("100")),
        sa.Column("description", sa.Text(), nullable=False),
        # Nullable: a step can be unassigned, or the whole task can be assigned
        # instead through task_contributor.
        sa.Column("contributor_id", sa.Text(),
                  sa.ForeignKey("contributor.contributor_id")),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'not_started'")),
        sa.Column("completed_date", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_by", sa.Integer()),
    )
    op.create_index("ix_task_step_task", "task_step", ["task_id"])
    # Finding everything assigned to one person is the query that justified a
    # table rather than a list stored on the task.
    op.create_index("ix_task_step_contributor", "task_step", ["contributor_id"])

    # ---- Who is responsible for the task as a whole ----------------------
    op.create_table(
        "task_contributor",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("contributor_id", sa.Text(),
                  sa.ForeignKey("contributor.contributor_id"), primary_key=True),
        sa.Column("added_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("added_by", sa.Integer()),
    )
    op.create_index("ix_task_contributor_person", "task_contributor",
                    ["contributor_id"])


def downgrade():
    op.drop_table("task_contributor")
    op.drop_table("task_step")
    op.drop_table("task")
    op.drop_table("task_type")
