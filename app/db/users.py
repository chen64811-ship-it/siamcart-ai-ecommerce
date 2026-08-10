"""Small user repository shared by SQLite and PostgreSQL."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.db.connection import connect_database


def get_user_by_email(db_path: str, email: str) -> dict[str, Any] | None:
    conn = connect_database(db_path)
    try:
        row = conn.execute(
            "SELECT user_id, email, password_hash, display_name, created_at "
            "FROM users WHERE LOWER(email) = LOWER(?)",
            (email.strip(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(db_path: str, user_id: str) -> dict[str, Any] | None:
    conn = connect_database(db_path)
    try:
        row = conn.execute(
            "SELECT user_id, email, password_hash, display_name, created_at "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_user(db_path: str, email: str, password_hash: str, display_name: str) -> dict[str, Any]:
    user_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = connect_database(db_path, write=True)
    try:
        conn.execute(
            "INSERT INTO users (user_id, email, password_hash, display_name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, email.strip().lower(), password_hash, display_name.strip(), created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "user_id": user_id,
        "email": email.strip().lower(),
        "display_name": display_name.strip(),
        "created_at": created_at,
    }
