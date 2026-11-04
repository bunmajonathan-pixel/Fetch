"""
views.py — saved views: a named set of filters on one analytics screen, kept per user.

A saved view stores only *which screen* and *which filters*, never results.  Opening a view
runs the normal analytics endpoint again, so the user's current permissions are applied each
time: a view saved before an account lost access to a company simply returns the usual 403.

Stored in its own SQLite file (``instance/views.db``) so that resetting the user database
does not delete people's views.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# The filter keys each screen accepts.  Anything else is dropped before saving.
SCREENS: dict[str, tuple[str, ...]] = {
    "contracts":  ("ticker", "status", "quarter", "category", "owner", "renewal", "sole_source"),
    "financials": ("ticker", "from", "to", "metric"),
    "behavior":   ("ticker", "year", "assessment"),
    "company":    ("ticker",),
}
MAX_PER_USER = 25
MAX_NAME = 60

_DB: Path | None = None


def init_views(path: str | Path) -> None:
    global _DB
    _DB = Path(path)
    _DB.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS views (
                        id       INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        screen   TEXT NOT NULL,
                        name     TEXT NOT NULL,
                        filters  TEXT NOT NULL,
                        created  REAL NOT NULL,
                        UNIQUE (username, screen, name))""")


def _connect() -> sqlite3.Connection:
    if _DB is None:
        raise RuntimeError("views.init_views() has not been called")
    c = sqlite3.connect(str(_DB))
    c.row_factory = sqlite3.Row
    return c


def clean(screen: str, name: str, filters: object) -> tuple[str, dict[str, str]]:
    """Validate and normalise; raises ValueError with a message fit to show the user."""
    if screen not in SCREENS:
        raise ValueError(f"screen must be one of {', '.join(SCREENS)}.")
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Give the view a name.")
    if len(name) > MAX_NAME:
        raise ValueError(f"A view name can be at most {MAX_NAME} characters.")
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object.")
    kept: dict[str, str] = {}
    for k in SCREENS[screen]:
        v = filters.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise ValueError(f"Filter '{k}' must be text or a number.")
        v = str(v).strip()
        if len(v) > 40:
            raise ValueError(f"Filter '{k}' is too long.")
        kept[k] = v
    return name, kept


def _out(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "screen": r["screen"], "name": r["name"],
            "filters": json.loads(r["filters"]), "created": r["created"]}


def save(username: str, screen: str, name: str, filters: dict[str, str]) -> dict:
    """Create the view, or replace the filters of the user's view with the same name on that screen."""
    with _connect() as c:
        exists = c.execute("SELECT id FROM views WHERE username=? AND screen=? AND name=?",
                           (username, screen, name)).fetchone()
        if not exists and c.execute("SELECT COUNT(*) FROM views WHERE username=?", (username,)).fetchone()[0] >= MAX_PER_USER:
            raise ValueError(f"You can keep at most {MAX_PER_USER} saved views. Delete one first.")
        if exists:
            c.execute("UPDATE views SET filters=?, created=? WHERE id=?", (json.dumps(filters), time.time(), exists["id"]))
            vid = exists["id"]
        else:
            vid = c.execute("INSERT INTO views (username, screen, name, filters, created) VALUES (?,?,?,?,?)",
                            (username, screen, name, json.dumps(filters), time.time())).lastrowid
        return _out(c.execute("SELECT * FROM views WHERE id=?", (vid,)).fetchone())


def list_views(username: str) -> list[dict]:
    with _connect() as c:
        return [_out(r) for r in c.execute("SELECT * FROM views WHERE username=? ORDER BY screen, name", (username,))]


def delete(username: str, view_id: int) -> bool:
    """Delete one of the user's own views.  Someone else's id behaves like a missing one."""
    with _connect() as c:
        return c.execute("DELETE FROM views WHERE id=? AND username=?", (view_id, username)).rowcount > 0
