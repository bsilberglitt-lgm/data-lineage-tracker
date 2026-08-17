"""Release notes, resources, schema snapshots, Manual Entry, extra flow fields.

Adds:

* ``app_release``      — versioned release notes, drafted then published
* ``resource`` +
  ``resource_version`` — documents and links attached to systems and flows,
                         with full upload history (files held in Postgres for
                         now; see CLAUDE.md for the planned move to S3)
* ``schema_snapshot``  — the previous state of a system's schema before each
                         .sql upload, so an upload can be reverted
* new ``data_flow`` fields: free-text detail for a frequency of "Other", plus
  Organization / Managed By / Data Steward to match systems
* new ``system`` fields for the Manual Entry type
* ``app_user.last_seen_release`` to drive the what's-new modal

Also renames the ``Manual Process`` system type to ``Manual Entry``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now())::text")


def upgrade():
    # ---- Release notes ---------------------------------------------------
    op.create_table(
        "app_release",
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text()),
        # Light markdown: headings, bullets, bold/italic, links.
        sa.Column("notes", sa.Text()),
        sa.Column("released_date", sa.Text()),
        sa.Column("is_published", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sort_key", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
    )
    op.create_index("ix_app_release_sort", "app_release", ["sort_key"])

    # Which release a user has already been shown. New accounts are stamped with
    # the current version so they don't get a modal about history they missed.
    op.add_column("app_user", sa.Column("last_seen_release", sa.Text()))

    # ---- Resources: documents and links ----------------------------------
    op.create_table(
        "resource",
        sa.Column("resource_id", sa.Integer(), primary_key=True, autoincrement=True),
        # Polymorphic by design: the same section appears on systems and flows.
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        # 'file' or 'link'
        sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'file'")),
        # Set for resources the application creates automatically (the Manual
        # Entry field list), whose label must not be edited or removed.
        sa.Column("label_locked", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0")),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
    )
    op.create_index("ix_resource_entity", "resource", ["entity_type", "entity_id"])

    op.create_table(
        "resource_version",
        sa.Column("version_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("resource_id", sa.Integer(), sa.ForeignKey("resource.resource_id"),
                  nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_current", sa.Integer(), nullable=False, server_default=sa.text("1")),
        # For links
        sa.Column("url", sa.Text()),
        # For files. The bytes live in their own column so listing resources
        # never has to read them; queries select explicit columns.
        sa.Column("filename", sa.Text()),
        sa.Column("content_type", sa.Text()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("file_data", sa.LargeBinary()),
        sa.Column("uploaded_at", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("uploaded_by", sa.Integer()),
        sa.Column("note", sa.Text()),
    )
    op.create_index("ix_resource_version_resource", "resource_version",
                    ["resource_id", "is_current"])

    # ---- Schema snapshots (revertible .sql uploads) ----------------------
    op.create_table(
        "schema_snapshot",
        sa.Column("snapshot_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("system_id", sa.Text(), sa.ForeignKey("system.system_id"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by", sa.Integer()),
        sa.Column("source_filename", sa.Text()),
        # The uploaded .sql that produced the *new* schema, kept so an upload can
        # be inspected or re-applied.
        sa.Column("source_sql", sa.Text()),
        # Full JSON of the schema as it stood BEFORE this upload, including every
        # governance override, so a revert restores hand-entered work.
        sa.Column("snapshot_json", sa.Text()),
        sa.Column("table_count", sa.Integer()),
        sa.Column("column_count", sa.Integer()),
        sa.Column("summary", sa.Text()),
        sa.Column("is_current", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_schema_snapshot_system", "schema_snapshot", ["system_id"])

    # ---- Data flow: timing detail and ownership --------------------------
    # frequency keeps its structured value; when it is "Other" this holds the
    # free-text description of when and under what conditions data moves.
    op.add_column("data_flow", sa.Column("frequency_detail", sa.Text()))
    op.add_column("data_flow", sa.Column("organization", sa.Text()))
    op.add_column("data_flow", sa.Column("managed_by", sa.Text()))
    op.add_column("data_flow", sa.Column("data_steward_id", sa.Text(),
                                         sa.ForeignKey("contributor.contributor_id")))

    # ---- System: Manual Entry fields -------------------------------------
    # 'Direct Entry' or 'File Upload'
    op.add_column("system", sa.Column("entry_type", sa.Text()))
    # Who does the entering, e.g. "District" or "State Help Desk staff"
    op.add_column("system", sa.Column("entered_by", sa.Text()))
    op.add_column("system", sa.Column("entry_validation", sa.Text()))
    op.add_column("system", sa.Column("entry_error_correction", sa.Text()))
    # The data store this manual entry feeds; drives the auto-created flow.
    op.add_column("system", sa.Column("target_system_id", sa.Text()))

    # ---- Rename the Manual Process type ----------------------------------
    op.execute("UPDATE system SET system_type = 'Manual Entry' "
               "WHERE system_type = 'Manual Process'")


def downgrade():
    op.execute("UPDATE system SET system_type = 'Manual Process' "
               "WHERE system_type = 'Manual Entry'")
    for col in ("target_system_id", "entry_error_correction", "entry_validation",
                "entered_by", "entry_type"):
        op.drop_column("system", col)
    for col in ("data_steward_id", "managed_by", "organization", "frequency_detail"):
        op.drop_column("data_flow", col)
    op.drop_index("ix_schema_snapshot_system", table_name="schema_snapshot")
    op.drop_table("schema_snapshot")
    op.drop_index("ix_resource_version_resource", table_name="resource_version")
    op.drop_table("resource_version")
    op.drop_index("ix_resource_entity", table_name="resource")
    op.drop_table("resource")
    op.drop_column("app_user", "last_seen_release")
    op.drop_index("ix_app_release_sort", table_name="app_release")
    op.drop_table("app_release")
