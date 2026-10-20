"""
audit.py — Append-only access log for every retrieve and sign-in event.

Each entry is a dict written as a JSON line to ``audit.log`` (next to server.py)
and kept in memory for the current process lifetime.

Log entry schema
----------------
{
  "ts":       "2026-01-15T14:32:01.123456",  # UTC ISO-8601
  "event":    "retrieve" | "signin" | "signout" | "refresh",
  "username": "NL-2048",
  "ip":       "127.0.0.1",                   # optional — supplied by caller
  "detail":   { ... }                        # event-specific payload
}
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# In-memory ring buffer (last 1 000 entries) — useful for a future /api/audit endpoint
_ENTRIES: list[dict] = []
_MAX_ENTRIES = 1_000

# File path — sits next to server.py; resolved lazily so tests don't need it to exist
_LOG_PATH: Path | None = None


def configure(log_path: str | Path) -> None:
    """Point the audit logger at a file.  Call once at server startup."""
    global _LOG_PATH
    _LOG_PATH = Path(log_path)
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def record(event: str, username: str, ip: str | None = "", detail: dict[str, Any] | None = None) -> None:
    """Append one log entry.  Never raises — logging must not break the request."""
    entry = {
        "ts":       datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "event":    event,
        "username": username,
        "ip":       ip or "",
        "detail":   detail or {},
    }

    # Memory buffer
    _ENTRIES.append(entry)
    if len(_ENTRIES) > _MAX_ENTRIES:
        _ENTRIES.pop(0)

    # File — one JSON line per entry
    if _LOG_PATH is not None:
        try:
            with _LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            log.warning("audit: could not write to %s", _LOG_PATH)


def recent(n: int = 100) -> list[dict]:
    """Return the last *n* in-memory entries (newest last)."""
    return list(_ENTRIES[-n:])
