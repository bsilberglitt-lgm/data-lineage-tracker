"""Definitions a SuperAdmin writes for individual record fields.

A field's name is often not enough. "Disclosure Avoidance" or "Regulatory
Driver" mean something precise to whoever set the policy and very little to
someone filling the form in for the first time, and the answer currently lives
in a document nobody opens. A definition written here appears as a tooltip
beside that field for every user.

**Keyed on (entity_type, column_name), not on the label.** The same column is
labelled differently depending on where it is drawn — `encryption_at_rest` is
"Encrypt at Rest" in the edit view and "Encryption at Rest" in the read view
and the Add modal — so a definition keyed on text would attach to one spelling
and be missing from the others. Seventeen of the seventy-seven fields have more
than one spelling. The column is what is actually stable.

`field_catalog.py`, generated from the UI, is what maps a label back to its
column, and is the authority on which (entity, column) pairs exist. Rows here
for a field that no longer exists are harmless: nothing reads them, and the
catalogue decides what is offered.

No foreign key to anything, deliberately. These describe the *schema* rather
than any record, so they survive every system and data flow being deleted.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "field_definition",
        sa.Column("entity_type", sa.Text(), primary_key=True),
        sa.Column("column_name", sa.Text(), primary_key=True),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("updated_date", sa.Text()),
        sa.Column("updated_by", sa.Integer()),
    )


def downgrade():
    op.drop_table("field_definition")
