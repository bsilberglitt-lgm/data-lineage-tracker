"""Deactivating a contributor who is no longer involved.

People leave, change roles, or turn out to have been recorded twice. Until now
the only way to tidy one away was to delete them, and deletion was refused
whenever a user account was linked — and would have hit a foreign key anyway
the moment they were named as an Owner or Data Steward anywhere. So in practice
there was no way at all, and the picker grew indefinitely.

**Deactivation rather than deletion**, matching how user accounts work. The
reasoning is the same: the audit trail refers to contributors by identifier, so
deleting one leaves entries pointing at a record that no longer exists, and a
governance record is exactly the kind of thing someone needs to look up two
years later when asking who signed off on something.

Three consequences of that choice, all deliberate:

- **Existing assignments are kept.** A system that named this person as its
  Data Steward goes on naming them, shown as deactivated. Clearing those would
  leave the system with no steward at all and no record that it ever had one
  outside the audit trail — which is the data loss deactivation exists to
  avoid. The gap stays visible so someone reassigns it on purpose.
- **The linked user account is untouched**, and so is the link. Deactivating a
  contributor is not deactivating a sign-in, and a reactivated contributor
  should not need an administrator to remember to reconnect them.
- **They drop out of the pickers**, so nobody new can be assigned to them,
  except on a record that already names them — where removing the current
  value from its own dropdown would make it impossible to see what it says.

Deactivated contributors are visible only to a SuperAdmin, greyed, and
reactivated by editing them.

INTEGER 0/1 rather than BOOLEAN, per the convention in CLAUDE.md. `app_user`
spells the same idea the same way.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "contributor",
        sa.Column("is_active", sa.Integer(), nullable=False,
                  server_default="1"))


def downgrade():
    op.drop_column("contributor", "is_active")
