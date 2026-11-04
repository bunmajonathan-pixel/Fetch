"""
auth.py — Session-based authentication.

login()          → validates credentials, returns an opaque session token.
validate_token() → returns the username if the token is valid, raises otherwise.
logout()         → invalidates the token immediately.

Tokens are random 32-byte hex strings stored in a TTL-aware in-memory map.
Default session lifetime: 3600 seconds (1 hour).
"""
from __future__ import annotations
import secrets
import time

import bcrypt

from .db_users import get_user


class AuthError(Exception):
    """Raised for any authentication or authorisation failure."""


# token → (username, expires_at)
_SESSIONS: dict[str, tuple[str, float]] = {}

SESSION_TTL: float = 3600.0  # seconds


_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


def login(username: str, password: str, ttl: float = SESSION_TTL) -> str:
    """
    Validate credentials and return a session token.

    Raises AuthError on bad username or wrong password.
    """
    user = get_user(username)
    if user is None:
        bcrypt.checkpw(password.encode()[:72], _DUMMY_HASH)   # same cost as a real check: no timing tell
        raise AuthError("Invalid username or password.")
    if not user.check_password(password):
        raise AuthError("Invalid username or password.")

    return issue_token(username, ttl)


def issue_token(username: str, ttl: float = SESSION_TTL) -> str:
    """Create a session for an already-authenticated user and return its token."""
    token = secrets.token_hex(32)
    _SESSIONS[token] = (username, time.monotonic() + ttl)
    return token


def validate_token(token: str) -> str:
    """
    Return the username associated with *token*.

    Raises AuthError if the token is missing or expired.
    """
    entry = _SESSIONS.get(token)
    if entry is None:
        raise AuthError("Session not found. Please log in.")

    username, expires_at = entry
    if time.monotonic() > expires_at:
        del _SESSIONS[token]
        raise AuthError("Session expired. Please log in again.")

    return username


def logout(token: str) -> None:
    """Invalidate a session token (no-op if already gone)."""
    _SESSIONS.pop(token, None)


def active_sessions() -> dict[str, str]:
    """Return {token: username} for all non-expired sessions (diagnostic use)."""
    now = time.monotonic()
    return {t: u for t, (u, exp) in _SESSIONS.items() if exp > now}


def revoke_user(username: str, keep: str | None = None) -> int:
    """End every session of *username* (except the token *keep*).  Returns how many were ended."""
    gone = [t for t, (u, _) in _SESSIONS.items() if u == username and t != keep]
    for t in gone:
        del _SESSIONS[t]
    return len(gone)
