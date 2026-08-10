"""Database connection compatibility for SQLite and PostgreSQL.

The established store repository uses DB-API style qmark parameters.  This
module keeps that interface stable for SQLite tests while adapting the same
parameterized statements to psycopg in production.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from app.config import DATABASE_URL, STORE_DB_PATH


def normalize_database_url(url: str) -> str:
    """Normalize Railway's legacy postgres scheme for modern drivers."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def database_url_for(db_path: str | None = None) -> str:
    """Resolve a database URL while preserving explicit SQLite test paths."""
    configured = normalize_database_url(os.getenv("DATABASE_URL", DATABASE_URL))
    configured_store_path = os.getenv("STORE_DB_PATH", STORE_DB_PATH)
    if configured.startswith(("postgresql://", "postgresql+psycopg://")):
        if db_path is None:
            return configured
        try:
            if Path(db_path).resolve() == Path(configured_store_path).resolve():
                return configured
        except (OSError, ValueError):
            pass
    path = db_path or configured_store_path
    return f"sqlite:///{Path(path).resolve().as_posix()}"


def is_postgres_url(url: str) -> bool:
    return normalize_database_url(url).startswith(
        ("postgresql://", "postgresql+psycopg://")
    )


class CompatRow(Mapping[str, Any]):
    """Mapping row that also supports the numeric access used by sqlite3.Row."""

    def __init__(self, columns: list[str], values: tuple[Any, ...]):
        self._columns = columns
        self._values = values
        self._data = dict(zip(columns, values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)


def _postgres_sql(sql: str) -> str:
    stripped = sql.strip().upper()
    if stripped == "BEGIN IMMEDIATE":
        return "BEGIN"
    return sql.replace("?", "%s")


class PostgresCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def execute(self, sql: str, params: Any = None) -> "PostgresCursor":
        self._cursor.execute(_postgres_sql(sql), params or ())
        return self

    def _row(self, values: tuple[Any, ...] | None) -> CompatRow | None:
        if values is None:
            return None
        columns = [getattr(col, "name", col[0]) for col in self._cursor.description]
        return CompatRow(columns, tuple(values))

    def fetchone(self) -> CompatRow | None:
        return self._row(self._cursor.fetchone())

    def fetchall(self) -> list[CompatRow]:
        rows = self._cursor.fetchall()
        if not rows:
            return []
        columns = [getattr(col, "name", col[0]) for col in self._cursor.description]
        return [CompatRow(columns, tuple(row)) for row in rows]


class PostgresConnection:
    dialect = "postgresql"

    def __init__(self, connection: Any):
        self._connection = connection

    def cursor(self) -> PostgresCursor:
        return PostgresCursor(self._connection.cursor())

    def execute(self, sql: str, params: Any = None) -> PostgresCursor:
        cursor = self.cursor()
        return cursor.execute(sql, params)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect_database(db_path: str | None = None, write: bool = False):
    """Open a SQLite or PostgreSQL connection with a common small interface."""
    url = database_url_for(db_path)
    if is_postgres_url(url):
        import psycopg

        driver_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        return PostgresConnection(psycopg.connect(driver_url))

    sqlite_path = url.removeprefix("sqlite:///")
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    if write:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
    return conn


def is_postgres_connection(connection: Any) -> bool:
    return getattr(connection, "dialect", None) == "postgresql"
