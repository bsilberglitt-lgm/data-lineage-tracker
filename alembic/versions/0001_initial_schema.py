"""Initial schema — ported from the original SQLite definition.

Notes on type choices:

* Boolean-ish columns stay ``INTEGER`` (0/1) rather than becoming ``BOOLEAN``.
  The JSON API and the frontend both treat these as 0/1 (the governance
  override selects compare against the strings "1"/"0"), so keeping INTEGER
  preserves the existing API contract exactly.
* ``created_date`` / ``updated_date`` stay ``TEXT`` holding ISO-8601 strings,
  matching what the application writes and what the frontend renders.
* Governance columns on ``schema_table`` / ``schema_column`` are nullable on
  purpose: NULL means "inherit from the parent", a value means "override".

Revision ID: 0001
Revises:
Create Date: 2026-07-30

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now())::text")


def upgrade():
    op.create_table(
        "stakeholder",
        sa.Column("stakeholder_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("organization_unit", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("role_type", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("governance_role", sa.Text()),
        sa.Column("governance_responsibilities", sa.Text()),
        sa.Column("training_status", sa.Text()),
        sa.Column("last_training_date", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    op.create_table(
        "system",
        sa.Column("system_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("system_type", sa.Text(), nullable=False),
        sa.Column("vendor", sa.Text()),
        sa.Column("technology", sa.Text()),
        sa.Column("environment", sa.Text()),
        sa.Column("managed_by", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("organization", sa.Text()),
        sa.Column("contains_pii", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ferpa_protected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'Active'")),
        sa.Column("notes", sa.Text()),
        sa.Column("x_pos", sa.Float(), server_default=sa.text("0")),
        sa.Column("y_pos", sa.Float(), server_default=sa.text("0")),
        # Security & Privacy (Framework Domain 2)
        sa.Column("data_classification", sa.Text()),
        sa.Column("access_method", sa.Text()),
        sa.Column("encryption_at_rest", sa.Text()),
        sa.Column("encryption_in_transit", sa.Text()),
        sa.Column("last_security_review", sa.Text()),
        sa.Column("has_audit_trail", sa.Integer(), server_default=sa.text("0")),
        # Data Management (Framework Domain 3)
        sa.Column("data_steward_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("retention_schedule", sa.Text()),
        sa.Column("destruction_method", sa.Text()),
        sa.Column("backup_frequency", sa.Text()),
        sa.Column("disaster_recovery", sa.Text()),
        sa.Column("data_standards", sa.Text()),
        # Data Use & Sharing (Framework Domain 4)
        sa.Column("authorized_users", sa.Text()),
        sa.Column("data_sharing_restrictions", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    op.create_table(
        "agreement",
        sa.Column("agreement_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("partner_agency", sa.Text(), nullable=False),
        sa.Column("agreement_type", sa.Text(), nullable=False),
        sa.Column("legal_basis", sa.Text()),
        sa.Column("data_shared", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Text()),
        sa.Column("expiration_date", sa.Text()),
        sa.Column("renewal_status", sa.Text()),
        sa.Column("dept_contact_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("partner_contact", sa.Text()),
        sa.Column("document_link", sa.Text()),
        sa.Column("notes", sa.Text()),
        # Governance fields (Framework Domains 2 & 4)
        sa.Column("compliance_framework", sa.Text()),
        sa.Column("data_classification", sa.Text()),
        sa.Column("disclosure_restrictions", sa.Text()),
        sa.Column("monitoring_frequency", sa.Text()),
        sa.Column("last_review_date", sa.Text()),
        sa.Column("review_status", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    op.create_table(
        "data_flow",
        sa.Column("flow_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source_system_id", sa.Text(), sa.ForeignKey("system.system_id"), nullable=False),
        sa.Column("destination_system_id", sa.Text(), sa.ForeignKey("system.system_id"), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False, server_default=sa.text("'Inbound'")),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("transfer_method", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("transformation_steps", sa.Text()),
        sa.Column("business_rules", sa.Text()),
        sa.Column("validations", sa.Text()),
        sa.Column("error_handling", sa.Text()),
        sa.Column("contains_pii", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ferpa_protected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("regulatory_driver", sa.Text()),
        sa.Column("agreement_id", sa.Text(), sa.ForeignKey("agreement.agreement_id")),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'Active'")),
        sa.Column("known_issues", sa.Text()),
        # Security & Privacy (Framework Domain 2)
        sa.Column("data_classification", sa.Text()),
        sa.Column("encryption_method", sa.Text()),
        sa.Column("disclosure_avoidance", sa.Text()),
        # Data Management (Framework Domain 3)
        sa.Column("data_quality_measures", sa.Text()),
        sa.Column("data_standard", sa.Text()),
        sa.Column("retention_at_destination", sa.Text()),
        # Data Use & Sharing (Framework Domain 4)
        sa.Column("legal_authority", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    op.create_table(
        "data_element",
        sa.Column("element_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.Text()),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text()),
        sa.Column("valid_values", sa.Text()),
        sa.Column("is_pii", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ferpa_protected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("source_of_truth", sa.Text(), sa.ForeignKey("system.system_id")),
        sa.Column("notes", sa.Text()),
        # Governance fields (Framework Domains 2, 3, 4)
        sa.Column("data_classification", sa.Text()),
        sa.Column("data_standard", sa.Text()),
        sa.Column("business_meaning", sa.Text()),
        sa.Column("data_owner_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("data_steward_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    op.create_table(
        "flow_element",
        sa.Column("flow_id", sa.Text(), sa.ForeignKey("data_flow.flow_id"), primary_key=True),
        sa.Column("element_id", sa.Text(), sa.ForeignKey("data_element.element_id"), primary_key=True),
        sa.Column("is_transformed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("transformation_note", sa.Text()),
    )

    # Database schema drill-down: physical tables belonging to a System (e.g. NEO).
    op.create_table(
        "schema_table",
        sa.Column("table_id", sa.Text(), primary_key=True),
        sa.Column("system_id", sa.Text(), sa.ForeignKey("system.system_id"), nullable=False),
        sa.Column("schema_name", sa.Text()),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("primary_key", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("x_pos", sa.Float(), server_default=sa.text("0")),
        sa.Column("y_pos", sa.Float(), server_default=sa.text("0")),
        # Governance overrides (NULL = inherit from parent system)
        sa.Column("data_classification", sa.Text()),
        sa.Column("contains_pii", sa.Integer()),
        sa.Column("ferpa_protected", sa.Integer()),
        sa.Column("data_owner_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("data_steward_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("retention_schedule", sa.Text()),
        sa.Column("access_restrictions", sa.Text()),
        sa.Column("legal_authority", sa.Text()),
        sa.Column("business_meaning", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    # Columns within a schema_table. NULL governance = inherit from the parent
    # table, which may itself inherit from the system.
    op.create_table(
        "schema_column",
        sa.Column("column_id", sa.Text(), primary_key=True),
        sa.Column("table_id", sa.Text(), sa.ForeignKey("schema_table.table_id"), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer()),
        sa.Column("data_type", sa.Text()),
        sa.Column("is_nullable", sa.Integer(), server_default=sa.text("1")),
        sa.Column("is_identity", sa.Integer(), server_default=sa.text("0")),
        sa.Column("is_pk", sa.Integer(), server_default=sa.text("0")),
        sa.Column("is_fk", sa.Integer(), server_default=sa.text("0")),
        sa.Column("default_value", sa.Text()),
        sa.Column("fk_target_schema", sa.Text()),
        sa.Column("fk_target_table", sa.Text()),
        sa.Column("fk_target_column", sa.Text()),
        sa.Column("fk_target_table_id", sa.Text()),
        sa.Column("fk_constraint", sa.Text()),
        # Governance overrides (NULL = inherit from parent table/system)
        sa.Column("data_classification", sa.Text()),
        sa.Column("contains_pii", sa.Integer()),
        sa.Column("ferpa_protected", sa.Integer()),
        sa.Column("data_owner_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("data_steward_id", sa.Text(), sa.ForeignKey("stakeholder.stakeholder_id")),
        sa.Column("retention_schedule", sa.Text()),
        sa.Column("access_restrictions", sa.Text()),
        sa.Column("legal_authority", sa.Text()),
        sa.Column("business_meaning", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
    )

    # Indexes supporting the drill-down queries.
    op.create_index("ix_schema_table_system_id", "schema_table", ["system_id"])
    op.create_index("ix_schema_column_table_id", "schema_column", ["table_id"])
    op.create_index("ix_data_flow_source", "data_flow", ["source_system_id"])
    op.create_index("ix_data_flow_destination", "data_flow", ["destination_system_id"])


def downgrade():
    op.drop_index("ix_data_flow_destination", table_name="data_flow")
    op.drop_index("ix_data_flow_source", table_name="data_flow")
    op.drop_index("ix_schema_column_table_id", table_name="schema_column")
    op.drop_index("ix_schema_table_system_id", table_name="schema_table")
    op.drop_table("schema_column")
    op.drop_table("schema_table")
    op.drop_table("flow_element")
    op.drop_table("data_element")
    op.drop_table("data_flow")
    op.drop_table("agreement")
    op.drop_table("system")
    op.drop_table("stakeholder")
