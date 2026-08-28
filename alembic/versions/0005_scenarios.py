"""Scenario planning.

A *scenario* is a private, non-live copy of the lineage diagram used to plan
architecture changes before anyone builds them. The live diagram is now called
**Current Architecture**; a scenario is shown with "(Scenario)" after its name.

The design, in one paragraph, because it governs every query in the app:

* Records that can differ between the live diagram and a scenario carry a
  nullable ``scenario_id``. **NULL means live.** Nothing else means live. Every
  read must scope on this column, which is why all of them go through one
  helper in ``scenarios.py`` rather than being written out by hand.
* ``origin_id`` records the live (or parent-scenario) row a scenario row was
  copied from. It is what makes a future "what would change if we did this?"
  comparison possible, and it is why deleting inside a scenario can still say
  *which* live thing is being retired.
* Identifiers stay unique across the whole table by prefixing scenario copies
  with the scenario: live ``SYS-001`` becomes ``S3-SYS-001`` in scenario 3. The
  live original is never touched, and the prefix makes a stray scenario row
  obvious on sight if one ever escapes into a live response.
* Shared reference data — schemas, data elements, contributors — is **not**
  copied when a scenario is created. A scenario reads live values until someone
  edits one, at which point that single record forks into the scenario
  (copy-on-write). Cloning is therefore cheap: the seeded schemas alone are
  ~3,300 columns, and copying them per scenario would multiply the largest
  tables in the database for data almost nobody edits.

``change_state`` marks how a record differs from live: ``proposed`` (new here),
``modified`` (copied then edited), ``removed`` (retired by this plan, still
drawn so the plan reads as a change set), or ``unchanged``. It is only ever
meaningful when ``scenario_id`` is set.

``is_hidden`` is deliberately *not* a fifth change state. The two answer
different questions: ``change_state`` is a statement about the plan ("this is
new", "this is being retired"), while ``is_hidden`` is a statement about the
view ("I am not looking at this right now"). Folding them together would mean
hiding a proposed system erased the fact that it was proposed.

**Hiding is cosmetic and must stay that way.** It exists so a scenario about
NEO and Federal Reporting is not buried under everything else. It must never
remove a record from analysis: when field-level gap analysis arrives, hiding a
system cannot make data stop flowing through it, or hiding noise would silently
manufacture clean results.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NOW_TEXT = sa.text("(now())::text")

# Tables that can hold a scenario-local copy of a record. Kept as data rather
# than repeated by hand so the upgrade, the downgrade, and the indexes cannot
# drift apart.
SCOPED_TABLES = [
    ("system", "system_id"),
    ("data_flow", "flow_id"),
    ("data_element", "element_id"),
    # Renamed from ``stakeholder`` in 0002.
    ("contributor", "contributor_id"),
    ("resource", "resource_id"),
    ("schema_snapshot", "snapshot_id"),
    ("schema_table", "table_id"),
    ("schema_column", "column_id"),
]

# These carry a scenario_id but no origin/change_state: a link row is not a
# thing a person proposes or retires on its own, it just belongs to a scenario.
LINK_TABLES = ["flow_element"]

# Records that appear on the diagram, and so can be hidden to reduce noise.
HIDEABLE_TABLES = ["system", "data_flow"]


def upgrade():
    # ---- The scenarios themselves ----------------------------------------
    op.create_table(
        "scenario",
        sa.Column("scenario_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        # Draft | Under review | Approved | Archived. Only Draft accepts edits;
        # the other three are frozen until someone returns it to Draft.
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'Draft'")),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("app_user.user_id"), nullable=False),
        # What it was started from, for provenance: 'live', 'scenario', 'blank'.
        sa.Column("origin_kind", sa.Text(), nullable=False,
                  server_default=sa.text("'blank'")),
        sa.Column("origin_scenario_id", sa.Integer()),
        # Soft delete. A deleted scenario vanishes from every list but is fully
        # restorable by its owner or a SuperAdmin — deleting a plan someone
        # spent weeks on should never be one misplaced click from permanent.
        sa.Column("deleted_at", sa.Text()),
        sa.Column("deleted_by_email", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("updated_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("created_by_email", sa.Text()),
    )
    op.create_index("ix_scenario_owner", "scenario", ["owner_user_id"])
    op.create_index("ix_scenario_live", "scenario", ["deleted_at"])

    # ---- Who can see and do what, *within* a scenario ---------------------
    # A scenario role is independent of the account's system role: a system
    # Viewer can be an editor here, and a system Editor can be a viewer here.
    # 'owner' is held by exactly one member and is the only role that can
    # manage membership. Administrators and SuperAdmins are not listed here —
    # their access comes from their system role and is resolved in code.
    op.create_table(
        "scenario_member",
        sa.Column("scenario_id", sa.Integer(),
                  sa.ForeignKey("scenario.scenario_id"), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("app_user.user_id"), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),   # owner | editor | viewer
        sa.Column("added_date", sa.Text(), nullable=False, server_default=NOW_TEXT),
        sa.Column("added_by_email", sa.Text()),
    )
    op.create_index("ix_scenario_member_user", "scenario_member", ["user_id"])

    # ---- Scope every record that can differ from live ---------------------
    for table, _pk in SCOPED_TABLES:
        op.add_column(table, sa.Column("scenario_id", sa.Integer()))
        op.add_column(table, sa.Column("origin_id", sa.Text()))
        op.add_column(table, sa.Column(
            "change_state", sa.Text(), nullable=False,
            server_default=sa.text("'unchanged'")))
        # Partial index: live reads are the common case and stay fast, and the
        # index only carries scenario rows.
        op.create_index(f"ix_{table}_scenario", table, ["scenario_id"],
                        postgresql_where=sa.text("scenario_id IS NOT NULL"))

    # Only what is drawn can be hidden. Reference data is shared, so hiding a
    # data element in one scenario would be meaningless.
    for table in HIDEABLE_TABLES:
        op.add_column(table, sa.Column(
            "is_hidden", sa.Integer(), nullable=False, server_default=sa.text("0")))

    for table in LINK_TABLES:
        op.add_column(table, sa.Column("scenario_id", sa.Integer()))
        op.create_index(f"ix_{table}_scenario", table, ["scenario_id"],
                        postgresql_where=sa.text("scenario_id IS NOT NULL"))

    # ---- Audit ------------------------------------------------------------
    # Scenario work is audited in the scenario; the live log stays clean apart
    # from lifecycle events (created, deleted, membership changed), which are
    # written with scenario_id NULL so they appear in Current Architecture.
    op.add_column("audit_event", sa.Column("scenario_id", sa.Integer()))
    op.create_index("ix_audit_event_scenario", "audit_event", ["scenario_id"],
                    postgresql_where=sa.text("scenario_id IS NOT NULL"))

    # ---- Notifications ----------------------------------------------------
    op.add_column("notification", sa.Column("scenario_id", sa.Integer()))


def downgrade():
    op.drop_column("notification", "scenario_id")
    op.drop_index("ix_audit_event_scenario", table_name="audit_event")
    op.drop_column("audit_event", "scenario_id")

    for table in LINK_TABLES:
        op.drop_index(f"ix_{table}_scenario", table_name=table)
        op.drop_column(table, "scenario_id")

    for table in HIDEABLE_TABLES:
        op.drop_column(table, "is_hidden")

    for table, _pk in SCOPED_TABLES:
        op.drop_index(f"ix_{table}_scenario", table_name=table)
        op.drop_column(table, "change_state")
        op.drop_column(table, "origin_id")
        op.drop_column(table, "scenario_id")

    op.drop_index("ix_scenario_member_user", table_name="scenario_member")
    op.drop_table("scenario_member")
    op.drop_index("ix_scenario_live", table_name="scenario")
    op.drop_index("ix_scenario_owner", table_name="scenario")
    op.drop_table("scenario")
