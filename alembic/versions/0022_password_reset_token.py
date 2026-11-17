"""Self-service password reset.

Until now a forgotten password meant Ben issuing a temporary one by hand. That
is workable for one department and is the first thing another state will hit,
so it is the thing email was added for (0.9.0).

**The token is stored as a SHA-256 hash, never in the clear.** It is a
bearer credential: anyone holding one can take over the account it belongs to
until it expires. Stored raw, a reader of the database — or of any of the 45
retained nightly backups — could use every live token in it. Hashed, the
database holds only enough to recognize a token somebody presents. SHA-256
rather than scrypt because, unlike a password, the token is 32 bytes of
`secrets.token_urlsafe` output and so has nothing to brute-force; the slow hash
a password needs would buy nothing and cost a comparison on every click.

**`token_hash` is the primary key**, which makes a collision impossible rather
than merely unlikely and means the lookup is the index.

**Single use, and short-lived.** `used_date` is set the moment a token is
spent, and the handler refuses one that is already spent rather than letting
the same link work twice — a link lives in an inbox, and inboxes get
forwarded. `expires_date` is written at creation from
`auth.RESET_TOKEN_MINUTES`, so the expiry travels with the row and changing
the setting later cannot silently extend tokens already issued.

**Nothing cascades from `app_user`, deliberately.** Deleting a user is not
something this app does — accounts are deactivated, never deleted (migration
0014 and the note in CLAUDE.md) — so a foreign key with a cascade would be
describing an event that cannot happen. `load_session` and the reset handler
both refuse an inactive user, which is what actually closes the door.

**`requested_ip` is kept for the audit trail**, not for rate limiting: the
limit is counted per account, because an attacker who wants to flood somebody's
inbox will not helpfully keep the same address.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "password_reset_token",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_date", sa.Text(), nullable=False),
        sa.Column("expires_date", sa.Text(), nullable=False),
        # NULL until spent. The handler reads this rather than deleting the
        # row, so a second click on the same link can be told apart from a
        # link that never existed — one is worth an explanation to the user,
        # the other is not.
        sa.Column("used_date", sa.Text()),
        sa.Column("requested_ip", sa.Text()),
    )
    # "How many has this account asked for lately" is the rate-limit question
    # and the only one asked of this table other than by primary key.
    op.create_index("ix_password_reset_user", "password_reset_token",
                    ["user_id", "created_date"])


def downgrade():
    op.drop_index("ix_password_reset_user", table_name="password_reset_token")
    op.drop_table("password_reset_token")
