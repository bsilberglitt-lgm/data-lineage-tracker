"""No-op alembic for the harness.

The app runs migrations at startup. In the harness the schema is already built
by the generated reset_db.py, from the same migration files, so upgrading is a
no-op — but the import has to succeed or the app will not boot."""


class _Command:
    @staticmethod
    def upgrade(cfg, revision):
        return None


command = _Command()
