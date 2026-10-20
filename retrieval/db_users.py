"""
db_users.py — SQLite-backed user store (drop-in replacement for the in-memory users.py store).

Usage
-----
Call `init_db(path)` once at server startup to create/open the database.
Then use `get_user`, `register_user` exactly like users.py.

Schema
------
CREATE TABLE users (
    username     TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    document_ids  TEXT NOT NULL   -- JSON array, e.g. '["BLFD","HDYN"]'
);
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import bcrypt

from retrieval.users import UserRecord   # reuse the dataclass


_DB_PATH: Path | None = None


def init_db(path: str | Path) -> None:
    """
    Create/open the SQLite database and ensure the users table exists.
    Seeds the default demo accounts if the table is empty.
    Call once at server startup.
    """
    global _DB_PATH
    _DB_PATH = Path(path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                document_ids  TEXT NOT NULL DEFAULT '[]'
            )
        """)

        # Seed default accounts only if the table is empty
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            _seed(conn)


def _connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("db_users.init_db() has not been called.")
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _seed(conn: sqlite3.Connection) -> None:
    """Insert the same default accounts as users.py."""
    defaults = [
        ("alice",   "alice123",    ["1", "3"]),
        ("bob",     "bob456",      ["2"]),
        ("NL-2048", "password1234",["BLFD", "HDYN", "MCLD"]),
        ("BLFD-01", "blfd2024",    ["BLFD"]),
        ("HDYN-01", "hdyn2024",    ["HDYN"]),
        ("MCLD-01", "mcld2024",    ["MCLD"]),
    ]
    conn.executemany(
        "INSERT INTO users (username, password_hash, document_ids) VALUES (?,?,?)",
        [(u, _hash(p), json.dumps(ids)) for u, p, ids in defaults],
    )


def _row_to_record(row: sqlite3.Row) -> UserRecord:
    record = UserRecord(
        username=row["username"],
        _password_hash=row["password_hash"],
        document_ids=json.loads(row["document_ids"]),
    )
    return record


# ---------------------------------------------------------------------------
# Public API  (matches retrieval/users.py interface)
# ---------------------------------------------------------------------------

def get_user(username: str) -> Optional[UserRecord]:
    """Return the UserRecord for *username*, or None if not found."""
    if _DB_PATH is None:
        # Fall back to in-memory store if DB not initialised (e.g. during tests)
        from retrieval.users import get_user as _mem_get
        return _mem_get(username)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return _row_to_record(row) if row else None


def register_user(username: str, password: str,
                  document_ids: list[str] | None = None) -> UserRecord:
    """Add a new user to the database. Raises ValueError if username already exists."""
    if _DB_PATH is None:
        from retrieval.users import register_user as _mem_reg
        return _mem_reg(username, password, document_ids)
    ids = json.dumps(document_ids or [])
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, document_ids) VALUES (?,?,?)",
                (username, _hash(password), ids),
            )
    except sqlite3.IntegrityError:
        raise ValueError(f"User '{username}' already exists.")
    return get_user(username)
