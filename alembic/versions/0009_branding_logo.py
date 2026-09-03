"""An uploadable logo for the sidebar and the sign-in screen.

A single row, always ``branding_id = 1``. There is one logo per deployment,
because each state runs its own instance — the same reasoning that makes
``preference`` a flat key/value table rather than something per-tenant.

**Why not a preference.** ``preference`` values are strings, and the settings
payload is fetched on every page load, including *unauthenticated* on the
sign-in screen. A base64 logo in there would be sent on every one of those.
The bytes therefore live here and are served by their own endpoint, which can
be cached and revalidated with an ETag; only the position and the
hide-the-title flag are preferences, where they sit beside ``app_title``.

**Why in PostgreSQL.** Consistent with ``resource_version.file_data`` and
``schema_snapshot.source_sql``: no extra vendor, and covered by the database's
existing backups and point-in-time recovery. When the planned S3 bucket
arrives, this moves with them — see the standing reminder in CLAUDE.md.

``content_type`` is what the *server* detected from the file's magic bytes, not
what the browser claimed, and is what the endpoint serves back. ``width`` and
``height`` are recorded at upload so the dimension cap is enforced once, on the
way in, rather than trusted later.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_branding",
        sa.Column("branding_id", sa.Integer(), primary_key=True),
        sa.Column("logo_data", sa.LargeBinary()),
        sa.Column("content_type", sa.Text()),
        sa.Column("filename", sa.Text()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        # Hashed from the bytes and used as the ETag and the cache-busting
        # token, so replacing a logo shows the new one immediately instead of
        # leaving a stale image that looks like a bug.
        sa.Column("etag", sa.Text()),
        sa.Column("uploaded_at", sa.Text()),
        sa.Column("uploaded_by", sa.Integer()),
    )


def downgrade():
    op.drop_table("app_branding")
