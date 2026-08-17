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
    def __init__(self, url: str):
        self._conn = psycopg.connect(url, row_factory=dict_row)

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
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()

def connect(url: str) -> PGConnection:
    return PGConnection(url)
