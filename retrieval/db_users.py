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
    document_ids  TEXT NOT NULL,  -- JSON array, e.g. '["BLFD","HDYN"]'
    owners        TEXT            -- JSON array of contract owners, or NULL = all owners
);
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import bcrypt

from .users import DEFAULT_ACCOUNTS, UserRecord   # shared dataclass + default accounts


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
                document_ids  TEXT NOT NULL DEFAULT '[]',
                owners        TEXT
            )
        """)
        # Databases created before record-level permissions have no owners column.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "owners" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN owners TEXT")   # NULL = all owners

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
    """Insert users.DEFAULT_ACCOUNTS (the single list shared with the in-memory store)."""
    conn.executemany(
        "INSERT INTO users (username, password_hash, document_ids, owners) VALUES (?,?,?,?)",
        [(u, _hash(p), json.dumps(ids), _owners_json(own)) for u, p, ids, own in DEFAULT_ACCOUNTS],
    )


def _owners_json(owners: list[str] | None) -> str | None:
    return None if owners is None else json.dumps(list(owners))


def _row_to_record(row: sqlite3.Row) -> UserRecord:
    owners = row["owners"]
    return UserRecord(
        username=row["username"],
        _password_hash=row["password_hash"],
        document_ids=json.loads(row["document_ids"]),
        owners=None if owners is None else json.loads(owners),
    )


# ---------------------------------------------------------------------------
# Public API  (matches retrieval/users.py interface)
# ---------------------------------------------------------------------------

def get_user(username: str) -> Optional[UserRecord]:
    """Return the UserRecord for *username*, or None if not found."""
    if _DB_PATH is None:
        # Fall back to in-memory store if DB not initialised (e.g. during tests)
        from .users import get_user as _mem_get
        return _mem_get(username)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return _row_to_record(row) if row else None


def register_user(username: str, password: str,
                  document_ids: list[str] | None = None,
                  owners: list[str] | None = None) -> UserRecord:
    """
    Add a new user to the database. Raises ValueError if username already exists.

    *owners* limits which contract owners (departments) the user can see within their
    tickers.  None (the default) means no limit; [] means no contracts at all.
    """
    if _DB_PATH is None:
        from .users import register_user as _mem_reg
        return _mem_reg(username, password, document_ids, owners)

    ids = document_ids or []
    pw_hash = _hash(password)
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, document_ids, owners) VALUES (?,?,?,?)",
                (username, pw_hash, json.dumps(ids), _owners_json(owners)),
            )
    except sqlite3.IntegrityError:
        raise ValueError(f"User '{username}' already exists.")
    return UserRecord(username=username, _password_hash=pw_hash, document_ids=ids,
                      owners=None if owners is None else list(owners))