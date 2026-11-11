"""System types as data, resource descriptions, and notifications.

* ``system_type`` — the list of system types becomes editable data rather than a
  hard-coded list, with a description and a color per type. Colors come from a
  vetted palette; every combination meets WCAG 2.1 AA (see ACCESSIBILITY.md).
* ``ETL Process`` and ``Internal`` are retired; systems using them become
  ``Data Store``.
* ``resource.description`` — a short description shown under each document/link.
* ``notification`` — the general in-app alert mechanism, first used to tell
  someone their change was reverted.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now())::text")

# name, description, fill, stroke, text, sort order
SEED_TYPES = [
    ("Data Store", "A database or repository that holds data at rest, such as an "
     "operational data store or a data warehouse.",
     "#e8daef", "#7d3c98", "#5b2c6f", 10),
    ("Destination", "A system that receives data as an end point — reporting, "
     "funding calculations, or an external agency.",
     "#c9e6d2", "#116039", "#0d3d22", 20),
    ("Manual Entry", "Data typed in or uploaded by a person rather than "
     "transferred automatically between systems.",
     "#fce5cd", "#b35c00", "#7a3e00", 30),
    ("Source", "A system where data originates, such as a district student "
     "information system or HR platform.",
     "#dce9fb", "#2f6fd0", "#123a6b", 40),
]


def upgrade():
    # ---- System types become editable data -------------------------------
    op.create_table(
        "system_type",
        sa.Column("type_name", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text()),
        sa.Column("fill_color", sa.Text(), nullable=False, server_default=sa.text("'#e2e6ea'")),
        sa.Column("stroke_color", sa.Text(), nullable=False, server_default=sa.text("'#4a5764'")),
        sa.Column("text_color", sa.Text(), nullable=False, server_default=sa.text("'#333f4a'")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_by", sa.Integer()),
    )

    conn = op.get_bind()
    for name, desc, fill, stroke, text, order in SEED_TYPES:
        conn.execute(
            sa.text("""INSERT INTO system_type
                         (type_name, description, fill_color, stroke_color,
                          text_color, sort_order)
                       VALUES (:n, :d, :f, :s, :t, :o)"""),
            {"n": name, "d": desc, "f": fill, "s": stroke, "t": text, "o": order})

    # Retire the two types being removed. Anything using them becomes a Data
    # Store, which is what CONNECT (the only real case) actually is.
    op.execute("UPDATE system SET system_type = 'Data Store' "
               "WHERE system_type IN ('ETL Process', 'Internal')")
    # Anything else unrecognized also lands somewhere valid rather than
    # rendering as an unknown type on the diagram.
    op.execute("UPDATE system SET system_type = 'Data Store' "
               "WHERE system_type NOT IN "
               "(SELECT type_name FROM system_type)")

    # ---- Resource descriptions -------------------------------------------
    op.add_column("resource", sa.Column("description", sa.Text()))

    # ---- Notifications ----------------------------------------------------
    op.create_table(
        "notification",
        sa.Column("notification_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("app_user.user_id"), nullable=False),
        # 'change_reverted', 'schema_reverted', … room for more later.
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        # Structured detail (the reverted fields), rendered by the client.
        sa.Column("detail_json", sa.Text()),
        sa.Column("entity_type", sa.Text()),
        sa.Column("entity_id", sa.Text()),
        sa.Column("related_event_id", sa.BigInteger()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by_email", sa.Text()),
        sa.Column("seen_at", sa.Text()),
    )
    op.create_index("ix_notification_user_unseen", "notification", ["user_id", "seen_at"])


def downgrade():
    op.drop_index("ix_notification_user_unseen", table_name="notification")
    op.drop_table("notification")
    op.drop_column("resource", "description")
    op.drop_table("system_type")
