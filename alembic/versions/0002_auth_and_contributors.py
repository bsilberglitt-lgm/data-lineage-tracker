"""Authentication, roles, audit trail; rename stakeholder -> contributor.

Structure added here:

* ``app_user``           — accounts, roles, hashed passwords
* ``user_session``       — server-side sessions (allows revoke / force-logout)
* ``login_attempt``      — drives progressive delays and temporary lockout
* ``audit_event`` +
  ``audit_field_change`` — append-only history of every change
* ``preference``         — SuperAdmin-editable global settings
* ``created_by`` / ``updated_by`` on the catalog tables

The ``stakeholder`` table becomes ``contributor`` and its identifiers are
renumbered from ``STK-nnn`` to ``CON-nnn``. The renumber inserts the new rows,
repoints every child reference, then deletes the old rows — so foreign keys
stay valid the whole way through and no constraints need dropping.

Order matters here: the rename happens first, because ``app_user`` has a
foreign key to ``contributor``.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now())::text")

# (table, columns referencing a contributor)
CONTRIBUTOR_REFS = [
    ("system", ["owner_id", "data_steward_id"]),
    ("agreement", ["dept_contact_id"]),
    ("data_flow", ["owner_id"]),
    ("data_element", ["data_owner_id", "data_steward_id"]),
    ("schema_table", ["data_owner_id", "data_steward_id"]),
    ("schema_column", ["data_owner_id", "data_steward_id"]),
]

# Columns carried across when the contributor rows are renumbered.
CONTRIBUTOR_COLUMNS = [
    "contributor_id", "name", "title", "organization_unit", "email", "phone",
    "role_type", "notes", "governance_role", "governance_responsibilities",
    "training_status", "last_training_date", "created_date", "updated_date",
]

# Catalog tables that gain authorship columns.
AUTHORED_TABLES = [
    "contributor", "system", "agreement", "data_flow",
    "data_element", "schema_table", "schema_column",
]


def upgrade():
    # ---- 1. Rename stakeholder -> contributor ---------------------------
    op.rename_table("stakeholder", "contributor")
    op.alter_column("contributor", "stakeholder_id", new_column_name="contributor_id")

    # ---- 2. Renumber STK-nnn -> CON-nnn, foreign keys intact throughout --
    cols = ", ".join(CONTRIBUTOR_COLUMNS)
    select_cols = ", ".join(
        "REPLACE(contributor_id, 'STK-', 'CON-')" if c == "contributor_id" else c
        for c in CONTRIBUTOR_COLUMNS
    )
    # 2a. Duplicate each row under its new identifier.
    op.execute(
        f"INSERT INTO contributor ({cols}) "
        f"SELECT {select_cols} FROM contributor WHERE contributor_id LIKE 'STK-%'"
    )
    # 2b. Repoint every child reference at the new identifier.
    for table, columns in CONTRIBUTOR_REFS:
        for column in columns:
            op.execute(
                f"UPDATE {table} SET {column} = REPLACE({column}, 'STK-', 'CON-') "
                f"WHERE {column} LIKE 'STK-%'"
            )
    # 2c. Remove the originals.
    op.execute("DELETE FROM contributor WHERE contributor_id LIKE 'STK-%'")

    # ---- 3. Accounts -----------------------------------------------------
    op.create_table(
        "app_user",
        sa.Column("user_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'Viewer'")),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("must_change_password", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        # Optional link to the governance directory: a contributor may have no
        # login, and a user may hold no governance role.
        sa.Column("contributor_id", sa.Text(),
                  sa.ForeignKey("contributor.contributor_id")),
        sa.Column("last_login_at", sa.Text()),
        sa.Column("password_changed_at", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
    )
    op.create_index("ix_app_user_email", "app_user", ["email"], unique=True)

    op.create_table(
        "user_session",
        sa.Column("session_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("app_user.user_id"),
                  nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text()),
        sa.Column("ip_address", sa.Text()),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_user_session_token", "user_session", ["token_hash"], unique=True)
    op.create_index("ix_user_session_user", "user_session", ["user_id"])

    op.create_table(
        "login_attempt",
        sa.Column("attempt_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("attempted_at", sa.Text(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ip_address", sa.Text()),
    )
    op.create_index("ix_login_attempt_email_time", "login_attempt",
                    ["email", "attempted_at"])

    # ---- 4. Audit (append-only) -----------------------------------------
    op.create_table(
        "audit_event",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Integer()),
        # Denormalised on purpose: history stays readable if an account changes.
        sa.Column("actor_email", sa.Text()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text()),
        sa.Column("entity_id", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("ip_address", sa.Text()),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_audit_event_time", "audit_event", ["occurred_at"])
    op.create_index("ix_audit_event_actor", "audit_event", ["actor_email"])
    op.create_index("ix_audit_event_entity", "audit_event", ["entity_type", "entity_id"])
    op.create_index("ix_audit_event_action", "audit_event", ["action"])

    op.create_table(
        "audit_field_change",
        sa.Column("change_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.BigInteger(),
                  sa.ForeignKey("audit_event.event_id"), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
    )
    op.create_index("ix_audit_change_event", "audit_field_change", ["event_id"])

    # ---- 5. Preferences --------------------------------------------------
    op.create_table(
        "preference",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text()),
        sa.Column("value_type", sa.Text(), nullable=False,
                  server_default=sa.text("'string'")),
        sa.Column("label", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.Text()),
        sa.Column("editable", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0")),
        sa.Column("updated_date", sa.Text()),
        sa.Column("updated_by", sa.Integer()),
    )

    # ---- 6. Authorship columns ------------------------------------------
    for table in AUTHORED_TABLES:
        op.add_column(table, sa.Column("created_by", sa.Integer()))
        op.add_column(table, sa.Column("updated_by", sa.Integer()))


def downgrade():
    for table in AUTHORED_TABLES:
        op.drop_column(table, "updated_by")
        op.drop_column(table, "created_by")
    op.drop_table("preference")
    op.drop_index("ix_audit_change_event", table_name="audit_field_change")
    op.drop_table("audit_field_change")
    for idx in ("ix_audit_event_action", "ix_audit_event_entity",
                "ix_audit_event_actor", "ix_audit_event_time"):
        op.drop_index(idx, table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("ix_login_attempt_email_time", table_name="login_attempt")
    op.drop_table("login_attempt")
    op.drop_index("ix_user_session_user", table_name="user_session")
    op.drop_index("ix_user_session_token", table_name="user_session")
    op.drop_table("user_session")
    op.drop_index("ix_app_user_email", table_name="app_user")
    op.drop_table("app_user")

    # Reverse the renumber, then the rename.
    cols = ", ".join(CONTRIBUTOR_COLUMNS)
    select_cols = ", ".join(
        "REPLACE(contributor_id, 'CON-', 'STK-')" if c == "contributor_id" else c
        for c in CONTRIBUTOR_COLUMNS
    )
    op.execute(
        f"INSERT INTO contributor ({cols}) "
        f"SELECT {select_cols} FROM contributor WHERE contributor_id LIKE 'CON-%'"
    )
    for table, columns in CONTRIBUTOR_REFS:
        for column in columns:
            op.execute(
                f"UPDATE {table} SET {column} = REPLACE({column}, 'CON-', 'STK-') "
                f"WHERE {column} LIKE 'CON-%'"
            )
    op.execute("DELETE FROM contributor WHERE contributor_id LIKE 'CON-%'")

    op.alter_column("contributor", "contributor_id", new_column_name="stakeholder_id")
    op.rename_table("contributor", "stakeholder")
