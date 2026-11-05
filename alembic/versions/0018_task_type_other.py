"""Somewhere to put the task type that is not on the list.

The four shipped task types are a starting point, and a SuperAdmin can add
more — but somebody creating a task should not be blocked because none of the
existing ones fit and they cannot edit the list themselves.

So **"Other" is always offered**, with a box to say what it actually is. The
free text goes here rather than into `task_type`, for two reasons:

* `task_type` stays a controlled list that a SuperAdmin owns. Letting a typed
  value land in it would make every Editor a type author by the back door,
  which is exactly the permission boundary `TaskTypesHandler` draws.
* The catalog can still group by type. Fifty one-off spellings in `task_type`
  would make that column useless, which is the thing a controlled list is for.

"Other" is a **constant the server accepts** (`TASK_TYPE_OTHER`), not a row in
`task_type`. A row would have to be seeded, and `ensure_task_types` only seeds
an empty table — so it would appear for new installations and never for an
existing one, which is the sort of split nobody would notice until a user asked
why they cannot see it.

Nullable with no default: it is meaningful only when the type is "Other", and
"" would be indistinguishable from "not told us yet".

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task", sa.Column("task_type_other", sa.Text()))


def downgrade():
    op.drop_column("task", "task_type_other")
