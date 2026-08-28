"""Add ``is_hidden`` to databases that ran an earlier build of 0005.

Migration 0005 gained the ``is_hidden`` column during development, after some
databases had already run it. Alembic records 0005 as applied and never re-runs
it, so those databases have the scenario tables but not that column, and the
application fails as soon as it queries one.

This migration exists purely to close that gap, and it is deliberately
idempotent: a database created from the current 0005 already has the column and
must not error here, while one created from the earlier build gains it.

**The lesson worth keeping:** once a migration has run *anywhere* — including
one laptop — it is spent. Change it and that environment silently diverges from
the code. Always add a new migration instead.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27

"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

HIDEABLE_TABLES = ["system", "data_flow"]


def upgrade():
    for table in HIDEABLE_TABLES:
        # IF NOT EXISTS keeps this safe on a database that already has it.
        op.execute(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS is_hidden INTEGER NOT NULL DEFAULT 0')


def downgrade():
    # Nothing to undo: 0005 owns the column on any database built from the
    # current code, and dropping it here would take it away from them too.
    pass
