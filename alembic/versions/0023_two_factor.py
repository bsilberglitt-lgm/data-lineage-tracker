"""Two-factor authentication: enrollment, recovery codes, challenges, devices.

`require_2fa` has existed as a locked-off preference since 0.9.0. This is the
flow behind it.

**Nothing secret is stored, which is the central decision here.** A TOTP secret
is symmetric — the server needs the same value the phone has — so unlike a
password it cannot be hashed. The obvious answer is to encrypt the column, and
that was the first plan. Two things argued against it. Python's standard
library has no symmetric cipher, so encrypting means adding a large compiled
dependency or composing a cipher out of HMAC by hand, and bespoke cryptography
in a project whose `SECURITY.md` is read by people paid to distrust exactly
that is a poor trade. And encryption still leaves a ciphertext in all 45
retained nightly backups.

So the secret is **derived** instead:

    secret = HMAC-SHA256(TWOFA_SECRET_KEY, b"totp" || user_id || salt)[:20]

`totp_salt` below is that salt. It is random, per enrollment, and **not
secret** — exactly like a password salt — which is why it sits in the clear. A
stolen database or backup therefore contains no secret at all, not even an
encrypted one: there is nothing to decrypt. The costs are the ones encryption
would also have had — the key must exist wherever the app runs, and losing or
rotating it voids every enrollment — and they are documented in `INSTALL.md`.

**Enrollment is not complete until a code has been proved.**
`totp_confirmed_date` is what says so, and it is set only after the user types
a working code. A salt alone means "started and never finished", which is the
state somebody is in if they close the window halfway. Without that
distinction, a half-enrolled account would be challenged for a code its owner
cannot produce — locked out by a feature they were in the middle of switching
on.

**No per-user method column, deliberately.** Whether an authenticator app is
available is `totp_confirmed_date IS NOT NULL`; whether an emailed code is
available is a question about the *deployment* — the `allow_email_2fa`
preference and whether mail actually works — not about the person. Storing a
chosen method per user would be a third thing to keep in step with those two,
and the first time they disagreed somebody would be offered a method the server
would then refuse.

Four tables' worth of notes follow, each on the thing that is easy to get
wrong.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    # --- Enrollment state on the account ----------------------------------
    #
    # Both nullable with no default: an existing account has not enrolled, and
    # NULL says that better than any sentinel would.
    op.add_column("app_user", sa.Column("totp_salt", sa.Text()))
    op.add_column("app_user", sa.Column("totp_confirmed_date", sa.Text()))

    # --- Recovery codes ----------------------------------------------------
    #
    # Ten single-use codes, shown once at enrollment and stored hashed. These
    # are the answer to "my phone is gone", and they matter most in the
    # deployment that has turned emailed codes off — where there is otherwise
    # no self-service route back in at all.
    #
    # `code_hash` is the primary key, so a collision is impossible rather than
    # unlikely and the lookup is the index. SHA-256 rather than scrypt for the
    # same reason the reset token uses it: 40 bits of random output has nothing
    # to brute-force, and this check happens on a path where somebody is
    # already locked out and anxious.
    #
    # `used_date` rather than deleting the row, so "you have already used that
    # one" can be told apart from "that is not a code" — one of those is worth
    # explaining to the person typing.
    op.create_table(
        "recovery_code",
        sa.Column("code_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_date", sa.Text(), nullable=False),
        sa.Column("used_date", sa.Text()),
    )
    # "How many does this account have left" is the only question asked of this
    # table other than by primary key, and it is asked on every sign-in that
    # reaches the second factor.
    op.create_index("ix_recovery_code_user", "recovery_code", ["user_id"])

    # --- The half-finished sign-in ----------------------------------------
    #
    # A challenge is what a correct password buys when a second factor is
    # required. **It is not a session and grants nothing**: it identifies one
    # sign-in attempt that has passed the first factor and is waiting on the
    # second.
    #
    # `code_hash` is NULL for the authenticator-app method — there is nothing
    # to store, because the code is computed from the derived secret at the
    # moment it is checked. It is set, hashed, for an emailed code, which
    # otherwise would be a live credential sitting in a row that ends up in a
    # backup.
    #
    # `attempts` is the load-bearing column. A six-digit emailed code is only
    # 10^6 wide and, unlike a TOTP code, it sits still until it expires rather
    # than moving every thirty seconds. The cap is what makes it safe, and it
    # is counted against the **challenge** rather than the code: five wrong
    # answers kill the whole challenge and the person signs in again from the
    # beginning.
    op.create_table(
        "twofa_challenge",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text()),
        sa.Column("created_date", sa.Text(), nullable=False),
        sa.Column("expires_date", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("consumed_date", sa.Text()),
        sa.Column("ip_address", sa.Text()),
    )
    # Rate-limiting emailed codes is a per-account question — an attacker
    # flooding somebody's inbox will not keep the same address — so this index
    # matches the one on `password_reset_token` and is read the same way.
    op.create_index("ix_twofa_challenge_user", "twofa_challenge",
                    ["user_id", "created_date"])

    # --- Trusted devices ---------------------------------------------------
    #
    # "Remember this device for 30 days" skips the second factor on one
    # browser. That makes it a second credential class, so it gets the same
    # treatment sessions get: the raw token lives only in the user's cookie,
    # the database holds a SHA-256 of it, and it can be revoked.
    #
    # `revoked_date` as well as `expires_date`, because the two are different
    # facts: one is the clock running out, the other is somebody deciding. A
    # password change revokes all of them — if you are changing your password
    # because you fear somebody else has it, a browser that no longer needs
    # your second factor is precisely what you want gone.
    #
    # `user_agent` is kept so the list a person revokes from says something
    # recognizable rather than offering three indistinguishable rows.
    op.create_table(
        "trusted_device",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_date", sa.Text(), nullable=False),
        sa.Column("expires_date", sa.Text(), nullable=False),
        sa.Column("last_seen_date", sa.Text()),
        sa.Column("revoked_date", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("ip_address", sa.Text()),
    )
    op.create_index("ix_trusted_device_user", "trusted_device", ["user_id"])


def downgrade():
    op.drop_index("ix_trusted_device_user", table_name="trusted_device")
    op.drop_table("trusted_device")
    op.drop_index("ix_twofa_challenge_user", table_name="twofa_challenge")
    op.drop_table("twofa_challenge")
    op.drop_index("ix_recovery_code_user", table_name="recovery_code")
    op.drop_table("recovery_code")
    op.drop_column("app_user", "totp_confirmed_date")
    op.drop_column("app_user", "totp_salt")
