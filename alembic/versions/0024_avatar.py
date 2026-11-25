"""A profile picture, shown instead of somebody's initials.

Ben asked for this: "Add ability to upload an image to your profile, which
would become their avatar instead of their initials next to their name and
role on the top toolbar."

**Four columns on `app_user` rather than a table of its own.** There is exactly
one picture per account, it is replaced rather than versioned, and it is
deleted with the account — which is every reason a separate table would have
existed. The logo made the same call for the same reasons (`app_branding` is
one row, always id 1), and following it keeps one way of storing an uploaded
image in this application rather than two.

**In the database, like every other upload here**, and for the reason in
reminder 7 of `CLAUDE.md`: no extra vendor, and the file is covered by the
nightly backups and point-in-time recovery already. It is on that reminder's
list to move to S3 with the rest. The caps are much lower than the resource
cap for the same reason the logo's are: this is drawn about 28 pixels across,
so anything larger is bytes carried through 45 backups to no effect.

**`avatar_etag` is what the browser is told about.** The image itself needs a
request of its own, so the user payload carries the digest and nothing else —
present means there is a picture, and the value changes when the picture does,
which is what makes a replaced avatar appear at once rather than after a cache
expires. The alternative, a boolean, would have left somebody staring at their
old photo and reloading.

Nothing here is nullable with a default: an account without a picture has NULL
in all four, which says "no picture" better than any sentinel.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_user", sa.Column("avatar_data", sa.LargeBinary()))
    # The content type is established by **inspecting the bytes** at upload
    # (`inspect_image`), never from the filename or the browser's claim, and
    # what is stored here is what the endpoint serves — so the two can never
    # disagree. See reminder 7: SVG is deliberately not accepted, because an
    # SVG can carry script and this is served from our own origin.
    op.add_column("app_user", sa.Column("avatar_type", sa.Text()))
    op.add_column("app_user", sa.Column("avatar_etag", sa.Text()))
    op.add_column("app_user", sa.Column("avatar_updated", sa.Text()))


def downgrade():
    op.drop_column("app_user", "avatar_updated")
    op.drop_column("app_user", "avatar_etag")
    op.drop_column("app_user", "avatar_type")
    op.drop_column("app_user", "avatar_data")
