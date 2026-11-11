"""Alembic environment.

The database URL comes from the DATABASE_URL environment variable so the same
migrations run unchanged locally (Docker Compose), on Render, and on any other
host later. No credentials are stored in the repository.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project root importable so we can reuse the URL normalization logic.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url():
    """Return the DATABASE_URL as a SQLAlchemy URL using the psycopg 3 driver.

    SQLAlchemy defaults to the legacy psycopg2 driver for a bare
    ``postgresql://`` URL. This project installs psycopg 3, so the driver has to
    be named explicitly as ``postgresql+psycopg://``. The application itself
    (db.py) uses the plain URL, because psycopg does not understand the
    SQLAlchemy ``+driver`` suffix.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Alembic needs it to know which database "
            "to migrate. Under Docker Compose it is set for you; on Render set "
            "it from your Postgres instance's Internal Database URL."
        )
    # Render/Heroku style -> standard scheme
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Pin the driver to psycopg 3 unless one was already specified
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# We manage the schema with explicit migrations rather than model autogeneration,
# so there is no MetaData to compare against.
target_metadata = None


def run_migrations_offline():
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
