"""Tiny sqlite3-compatible surface used by Innovate Pitch's existing server code.

This module lets the current application logic keep its sqlite-style conn.execute(...)
API while storing all data in PostgreSQL/Neon.
"""
from __future__ import annotations

import re
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
        # `conn` is a raw psycopg connection, owned by the module-level pool
        # below (`connect()`), not by this wrapper. This wrapper is cheap and
        # created fresh per-request; the underlying TCP/TLS connection to
        # Neon is what actually gets reused across requests in a warm
        # Vercel container.
        self._conn = conn

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> CompatCursor:
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
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._conn.execute(statement)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Deliberately a no-op: the raw connection is owned by the
        # module-level pool in `connect()` and is kept open so the *next*
        # request on this same warm serverless container can reuse it
        # instead of paying a fresh TCP+TLS+auth handshake to Neon.
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        global _shared_conn
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except Exception:
            # Connection is in a bad state (e.g. Neon closed it server-side
            # after being idle). Drop it so the next call reconnects instead
            # of repeatedly failing on a dead connection.
            try:
                self._conn.close()
            except Exception:
                pass
            _shared_conn = None
            if exc_type is None:
                raise


# One physical connection per warm container, reused across requests.
# `db_connect()` in server.py calls this once per request; we hand back a
# thin wrapper around the same underlying psycopg connection whenever it's
# still alive, instead of reconnecting to Neon from scratch every time.
_shared_conn: "psycopg.Connection | None" = None


def connect(url: str) -> PGConnection:
    global _shared_conn
    if _shared_conn is None or _shared_conn.closed:
        _shared_conn = psycopg.connect(
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
    else:
        try:
            # Cheap liveness check — a stale/half-closed connection from a
            # previous invocation should be replaced, not reused blindly.
            _shared_conn.execute("SELECT 1")
        except Exception:
            try:
                _shared_conn.close()
            except Exception:
                pass
            _shared_conn = psycopg.connect(
                url,
                row_factory=dict_row,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
    return PGConnection(_shared_conn)