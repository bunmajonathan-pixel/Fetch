"""
users.py — In-memory user store with hashed passwords and per-user document ownership.

In production replace _USER_DB with a real database lookup.
Passwords are stored as bcrypt hashes; never store plain text.
"""
from __future__ import annotations
import bcrypt
from dataclasses import dataclass, field
from typing import Optional


def _hash(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check(password: str, hashed: str) -> bool:
    """Constant-time bcrypt verification."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


@dataclass
class UserRecord:
    username: str
    _password_hash: str
    document_ids: list[str] = field(default_factory=list)

    def check_password(self, password: str) -> bool:
        return _check(password, self._password_hash)


# ---------------------------------------------------------------------------
# Seed store — replace or extend at runtime via register_user()
# ---------------------------------------------------------------------------
_USER_DB: dict[str, UserRecord] = {
    # Unit-test users (document IDs)
    "alice":   UserRecord("alice",   _hash("alice123"),     ["1", "3"]),
    "bob":     UserRecord("bob",     _hash("bob456"),       ["2"]),
    # Single-company analyst accounts
    "BLFD-01": UserRecord("BLFD-01", _hash("blfd2024"),     ["BLFD"]),
    "HDYN-01": UserRecord("HDYN-01", _hash("hdyn2024"),     ["HDYN"]),
    "MCLD-01": UserRecord("MCLD-01", _hash("mcld2024"),     ["MCLD"]),
}


def get_user(username: str) -> Optional[UserRecord]:
    return _USER_DB.get(username)


def register_user(username: str, password: str,
                  document_ids: list[str] | None = None) -> UserRecord:
    """Add a new user to the store (runtime only — not persisted)."""
    if username in _USER_DB:
        raise ValueError(f"User '{username}' already exists.")
    record = UserRecord(username, _hash(password), document_ids or [])
    _USER_DB[username] = record
    return record
