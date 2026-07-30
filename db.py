"""Database layer — PostgreSQL via psycopg 3.

This module provides a thin compatibility shim so the rest of the application
can keep using the sqlite3-style API it was written against:

    conn = get_db()
    row = conn.execute("SELECT * FROM system WHERE system_id=?", (sid,)).fetchone()
    conn.commit()
    conn.close()

Two differences between sqlite3 and psycopg are handled here so that no
application code has to change:

1. **Placeholders.** sqlite3 uses ``?``; psycopg uses ``%s``. ``_translate()``
   rewrites them, correctly skipping any ``?`` that appears inside a SQL string
   literal.
2. **Row access.** ``sqlite3.Row`` supports both ``row['col']`` and ``row[0]``.
   The ``Row`` class below does the same, and is a real ``dict`` so that
   ``dict(row)`` continues to work.

Connections come from a pool. ``close()`` returns the connection to the pool
rather than actually closing it.
"""

import os

from psycopg_pool import ConnectionPool

__all__ = ["get_db", "get_database_url", "close_pool", "Row"]


# ---------------------------------------------------------------------------
# Connection URL
# ---------------------------------------------------------------------------

def get_database_url():
    """Return the configured database URL.

    Render (and Heroku) historically expose ``postgres://`` URLs, which newer
    drivers reject; normalise those to ``postgresql://``.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set.\n"
            "  Local (Docker Compose): this is set for you in docker-compose.yml.\n"
            "  Render: add it from your Postgres instance's Internal Database URL.\n"
            "  Manual: export DATABASE_URL=postgresql://user:pass@host:5432/dbname"
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # psycopg does not understand SQLAlchemy's "+driver" suffix (Alembic adds
    # one); strip it so the same DATABASE_URL works for both.
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    return url


# ---------------------------------------------------------------------------
# Row type (supports both row['col'] and row[0], like sqlite3.Row)
# ---------------------------------------------------------------------------

class Row(dict):
    """A dict that also supports positional access by column index."""

    __slots__ = ("_cols",)

    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._cols = cols

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._cols[key])
        return super().__getitem__(key)


def _row_factory(cursor):
    cols = [c.name for c in (cursor.description or [])]

    def make_row(values):
        return Row(cols, values)

    return make_row


# ---------------------------------------------------------------------------
# Placeholder translation
# ---------------------------------------------------------------------------

def _translate(sql, has_params):
    """Rewrite sqlite-style ``?`` placeholders into psycopg ``%s`` placeholders.

    A ``?`` inside a single-quoted SQL string literal is left alone. When
    parameters are supplied, literal ``%`` characters are doubled, because
    psycopg performs ``%``-style interpolation in that case.
    """
    out = []
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if in_string:
            # '' is an escaped single quote inside a SQL string literal
            if ch == "'" and i + 1 < n and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            if ch == "'":
                in_string = False
            elif ch == "%" and has_params:
                out.append("%")  # doubled below
            out.append(ch)
            i += 1
            continue

        if ch == "'":
            in_string = True
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%" and has_params:
            out.append("%%")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_database_url(),
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_MAX", "5")),
            max_lifetime=30 * 60,
            kwargs={"row_factory": _row_factory},
            open=True,
        )
    return _pool


def close_pool():
    """Close the pool (used by scripts that exit cleanly)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


class Connection:
    """sqlite3-like wrapper around a pooled psycopg connection."""

    def __init__(self, raw, pool):
        self._raw = raw
        self._pool = pool
        self._released = False

    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        cur.execute(_translate(sql, bool(params)), params or None)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._raw.cursor()
        cur.executemany(_translate(sql, True), list(seq_of_params))
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        """Return the connection to the pool."""
        if not self._released:
            self._released = True
            try:
                self._pool.putconn(self._raw)
            except Exception:
                pass

    # Safety net: if a handler raises before calling close(), make sure the
    # connection still finds its way back to the pool when garbage collected.
    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self.rollback()
            except Exception:
                pass
        self.close()
        return False


def get_db():
    """Borrow a connection from the pool (sqlite3-compatible interface)."""
    pool = _get_pool()
    return Connection(pool.getconn(), pool)
