"""
users.py — UserRecord, the default demo accounts, and an in-memory fallback store.

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
    # Record-level limit: contract owners (departments) this user may see.
    # None = no limit (every owner within their tickers); [] = nothing.
    owners: Optional[list[str]] = None
    # Can manage accounts and read the audit log.  Says nothing about data access:
    # an admin sees company data only for the tickers in document_ids, like anyone else.
    is_admin: bool = False

    def check_password(self, password: str) -> bool:
        return _check(password, self._password_hash)


# ---------------------------------------------------------------------------
# Default accounts — the single definition used to seed BOTH stores
# (the in-memory fallback below and the SQLite store in db_users.py).
# username, plain-text demo password, document_ids (tickers or test doc IDs),
# owners (None = all owners; otherwise only contracts owned by these departments)
# ---------------------------------------------------------------------------
DEFAULT_ACCOUNTS: list[tuple[str, str, list[str], Optional[list[str]]]] = [
    # Unit-test users (document IDs)
    ("alice",    "alice123",     ["1", "3"], None),
    ("bob",      "bob456",       ["2"],      None),
    # Company-wide analyst accounts (all departments' contracts)
    ("BLFD-01",  "blfd2024",     ["BLFD"],   None),
    ("HDYN-01",  "hdyn2024",     ["HDYN"],   None),
    ("MCLD-01",  "mcld2024",     ["MCLD"],   None),
    # Department analysts: same company, but only their own department's contracts
    ("BLFD-LOG", "blfdlog2024",  ["BLFD"],   ["Logistics"]),
    ("HDYN-ENG", "hdyneng2024",  ["HDYN"],   ["Engineering"]),
    ("MCLD-INF", "mcldinf2024",  ["MCLD"],   ["Infrastructure"]),
    # Multi-company account used by tests/test_server.py
    ("NL-2048",  "password1234", ["BLFD", "HDYN", "MCLD"], None),
]

# Usernames among DEFAULT_ACCOUNTS that are administrators.  The demo admin has no
# document_ids, so it can manage accounts but cannot read any company's data.
DEFAULT_ADMINS = {"ADM-01"}
DEFAULT_ACCOUNTS.append(("ADM-01", "admin2024", [], None))

# In-memory fallback store (used only when db_users.init_db() has not been called).
_USER_DB: dict[str, UserRecord] = {
    u: UserRecord(u, _hash(p), list(ids), None if own is None else list(own), u in DEFAULT_ADMINS)
    for u, p, ids, own in DEFAULT_ACCOUNTS
}


def get_user(username: str) -> Optional[UserRecord]:
    return _USER_DB.get(username)


def register_user(username: str, password: str,
                  document_ids: list[str] | None = None,
                  owners: list[str] | None = None) -> UserRecord:
    """Add a new user to the store (runtime only — not persisted)."""
    if username in _USER_DB:
        raise ValueError(f"User '{username}' already exists.")
    record = UserRecord(username, _hash(password), document_ids or [], owners)
    _USER_DB[username] = record
    return record
