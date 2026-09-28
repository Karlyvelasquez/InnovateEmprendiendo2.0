"""Tiny sqlite3-compatible surface used by Innovate Pitch's existing server code.

This module lets the current application logic keep its sqlite-style conn.execute(...)
API while storing all data in PostgreSQL/Neon.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

Connection = Any
Row = Any
IntegrityError = psycopg.IntegrityError

class CompatRow(dict):
    """Mapping row that also supports SQLite-style integer indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            try:
                return list(self.values())[key]
            except IndexError:
                raise IndexError(key)
        return super().__getitem__(key)

class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, sql: str, params: Iterable[Any] | None = None):
        self._cursor.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return CompatRow(row) if row is not None else None

    def fetchall(self):
        return [CompatRow(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield CompatRow(row)

    def close(self):
        return self._cursor.close()

def _translate_sql(sql: str) -> str:
    # The existing project uses SQLite's ? placeholders.
    sql = sql.replace("?", "%s")
    # SQLite INSERT OR IGNORE -> PostgreSQL equivalent.
    sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+", "INSERT ", sql, flags=re.I)
    if re.match(r"^\s*INSERT\s+", sql, flags=re.I) and not re.search(r"\bON\s+CONFLICT\b", sql, flags=re.I):
        # All INSERT targets in this application have an auto-generated id.
        # RETURNING lets the existing code keep using cursor.lastrowid.
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING RETURNING id"
    return sql

class PGConnection:
    def __init__(self, conn):
        # `conn` is a raw psycopg connection borrowed from the module-level
        # pool below (`connect()`). This wrapper is cheap and created fresh
        # per request; leaving the `with` block hands the connection back to
        # the pool so the next request skips the TCP+TLS+auth handshake.
        self._conn = conn
        # Per-request cache for read helpers in server.py (e.g. the ranking
        # computation). Any write through this wrapper clears it, so cached
        # values never outlive a change made in the same request.
        self.cache: dict[Any, Any] = {}

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> CompatCursor:
        if not re.match(r"^\s*SELECT\b", sql, flags=re.I):
            self.cache.clear()
        sql = _translate_sql(sql)
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            wrapped = CompatCursor(cursor)
            if re.match(r"^\s*INSERT\s+", sql, flags=re.I):
                try:
                    row = cursor.fetchone()
                    wrapped.lastrowid = row["id"] if row is not None and "id" in row else None
                except psycopg.ProgrammingError:
                    wrapped.lastrowid = None
            return wrapped
        except Exception:
            cursor.close()
            raise

    def executescript(self, script: str) -> None:
        # Schema is supplied as PostgreSQL-compatible DDL. Execute statements
        # one by one so the existing application can keep this call site.
        self.cache.clear()
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._conn.execute(statement)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Deliberately a no-op: the connection goes back to the pool when the
        # `with db_connect() as conn:` block ends (see __exit__).
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except Exception:
            # Connection is in a bad state (e.g. Neon closed it server-side
            # after being idle). Drop it instead of returning it to the pool.
            _discard(self._conn)
            if exc_type is None:
                raise
            return
        _release(self._conn)


# Small pool of idle connections shared by ALL threads. ThreadingHTTPServer
# (used when running `python server.py` locally) spawns a new thread per
# request, so a per-thread connection was never reused locally: every request
# paid a fresh TCP+TLS+auth handshake to Neon (~2 s from Colombia). Each
# connection is still used by only one request at a time: it is taken out of
# the pool while a request holds it, which keeps psycopg's "one thread per
# connection" rule. On Vercel a warm container simply reuses the one idle
# connection it keeps.
_POOL_MAX_IDLE = 5
# Only verify a pooled connection with a round trip if it sat idle this long;
# checking on every request would add a full round trip to Neon each time.
_POOL_CHECK_AFTER_SECONDS = 60
_pool_lock = threading.Lock()
_idle: list[tuple["psycopg.Connection", float]] = []


def _open_connection(url: str) -> "psycopg.Connection":
    return psycopg.connect(
        url,
        row_factory=dict_row,
        connect_timeout=10,
        # Keep the TCP connection alive through NAT/proxies between
        # requests instead of it silently dying while idle.
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def _discard(conn: "psycopg.Connection") -> None:
    try:
        conn.close()
    except Exception:
        pass


def _release(conn: "psycopg.Connection") -> None:
    if conn.closed or conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        _discard(conn)
        return
    with _pool_lock:
        if len(_idle) < _POOL_MAX_IDLE:
            _idle.append((conn, time.monotonic()))
            return
    _discard(conn)


def connect(url: str) -> PGConnection:
    while True:
        with _pool_lock:
            conn, last_used = _idle.pop() if _idle else (None, 0.0)
        if conn is None:
            return PGConnection(_open_connection(url))
        if conn.closed:
            continue
        if time.monotonic() - last_used > _POOL_CHECK_AFTER_SECONDS:
            try:
                # Cheap liveness check for a connection that sat idle long
                # enough for Neon or a proxy to have dropped it.
                conn.execute("SELECT 1")
            except Exception:
                _discard(conn)
                continue
        return PGConnection(conn)
