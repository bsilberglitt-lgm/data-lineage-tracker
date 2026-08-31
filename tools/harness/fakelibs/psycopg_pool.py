"""Stand-in for psycopg's connection pool, backed by SQLite.

Lets the app be exercised without PostgreSQL, in a sandbox where PyPI is
unavailable. Only what db.py uses. Two details matter and are easy to get wrong:

* psycopg calls ``row_factory(cursor)`` ONCE per query and gets back a maker
  applied to each row's values. sqlite3's own ``row_factory`` works
  differently, so the two are bridged here rather than assigned across.
* psycopg's ``cursor.description`` entries expose ``.name``; sqlite3's are
  plain tuples, and db.py reads ``.name``.

This is a test double, not a shim the app ships with. Anything it gets wrong
is a way for the harness to disagree with production — see the note in
CLAUDE.md about what the harness cannot prove.
"""
import os, sqlite3

DB_FILE = os.environ.get("FAKE_SQLITE", "/tmp/h2/test.db")


class _Col:
    __slots__ = ("name",)
    def __init__(self, name): self.name = name


class _Cur:
    def __init__(self, raw, row_factory=None):
        self._raw, self._rf, self._make = raw, row_factory, None

    @property
    def description(self):
        d = self._raw.description
        return [_Col(c[0]) for c in d] if d else d

    def execute(self, sql, params=None):
        self._raw.execute(sql.replace('%s', '?').replace('%%', '%'), params or ())
        self._make = self._rf(self) if self._rf else None
        return self

    def executemany(self, sql, seq):
        self._raw.executemany(sql.replace('%s', '?').replace('%%', '%'), list(seq))
        self._make = None
        return self

    def _wrap(self, row):
        if row is None: return None
        return self._make(row) if self._make else row

    def fetchone(self): return self._wrap(self._raw.fetchone())
    def fetchall(self): return [self._wrap(r) for r in self._raw.fetchall()]
    def __iter__(self): return iter(self.fetchall())
    @property
    def rowcount(self): return self._raw.rowcount


class _Conn:
    def __init__(self, row_factory=None):
        self._c = sqlite3.connect(DB_FILE)
        self._c.execute("PRAGMA foreign_keys=ON")
        self._rf = row_factory

    def cursor(self, *a, **k): return _Cur(self._c.cursor(), self._rf)
    def execute(self, sql, params=None): return self.cursor().execute(sql, params)
    def commit(self): self._c.commit()
    def rollback(self): self._c.rollback()
    def close(self):
        try: self._c.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class ConnectionPool:
    def __init__(self, *a, **k):
        self._rf = (k.get("kwargs") or {}).get("row_factory")
    def open(self, *a, **k): pass
    def close(self, *a, **k): pass
    def wait(self, *a, **k): pass
    def getconn(self): return _Conn(self._rf)
    def putconn(self, conn): conn.close()
    def connection(self): return _Conn(self._rf)
