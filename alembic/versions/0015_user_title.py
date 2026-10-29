"""A job title on the user account.

The sign-in account knew a person's name and email but not what they do, so
every screen that showed who did something showed a name with no context.
Contributors already had a `title`, which meant a person who was both a
contributor and a user could carry two titles that disagreed with each other.

**The account is now the source of truth.** A contributor with a linked
`app_user` shows that account's title; a contributor with no account keeps its
own `title`, because plenty of contributors are people who will never sign in.
That is the one place the contributor column is still read.

**No data migration.** Ben cleared every contributor title in production before
this shipped, so there is nothing to copy up and no chance of two values
disagreeing on day one. Had there been data, this migration would have needed
to copy each linked contributor's title onto its account first — without that
step the titles would simply have stopped being displayed.

Nullable with no default: a title is genuinely optional, and "" would be
indistinguishable from "not told us yet" in a column people will filter on
later.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_user", sa.Column("title", sa.Text()))


def downgrade():
    op.drop_column("app_user", "title")
