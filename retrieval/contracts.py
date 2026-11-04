"""
contracts.py — supplier-contract store and expiry analytics.

The contract rows live in a SQLite table that is (re)built from a CSV
(data/Supplier_Contracts/contracts.csv) whenever the CSV's hash changes, so the
database is only a derived, query-friendly copy of the CSV.

Permissions are enforced HERE, in the SQL, not left to the caller:
every query takes the caller's allowed ``tickers`` and ``owners`` and adds
``ticker IN (...)`` / ``owner IN (...)`` to the WHERE clause.  User-chosen
filters are ANDed on top, so they can only narrow what the scope already allows.

    tickers  the companies the user may see (their document_ids)
    owners   None  -> every owner within those companies
             [..]  -> only those owners (record-level limit, e.g. ["Logistics"])
             []    -> nothing

Statuses in the data are "as of" AS_OF; days-to-expiry is measured from that date.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

AS_OF = date(2026, 11, 4)                    # the date the sample statuses are valid for
STATUSES = ("Active", "Expired", "Renewed", "Terminated")
MAX_ROWS = 1000                              # cap on contract rows returned per request

_QUARTER_RE = re.compile(r"^Q([1-4])-(\d{4})$")
_COLUMNS = ("contract_id", "ticker", "supplier", "category", "owner",
            "start_date", "expiry_date", "status", "annual_value_k")

_DB_PATH: Path | None = None


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def quarter_of(d: date) -> str:
    return f"Q{(d.month - 1) // 3 + 1}-{d.year}"


def quarter_label(q: str) -> str:
    return q.replace("-", " ")


def quarter_bounds(q: str) -> tuple[date, date]:
    m = _QUARTER_RE.match(q)
    if not m:
        raise ValueError(f"Invalid quarter {q!r}; expected e.g. Q4-2026.")
    n, y = int(m.group(1)), int(m.group(2))
    start = date(y, 3 * (n - 1) + 1, 1)
    end = (date(y + 1, 1, 1) if n == 4 else date(y, 3 * n + 1, 1))
    return start, date.fromordinal(end.toordinal() - 1)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("contracts.init_contracts() has not been called.")
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_contracts(db_path: str | Path, csv_path: str | Path) -> int:
    """
    Create/open the contracts database and load *csv_path* into it when the CSV is
    new or has changed.  Returns the number of contracts in the table.
    A missing CSV leaves an empty table (analytics then simply returns nothing).
    """
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    csv_path = Path(csv_path)

    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contracts (
                contract_id    TEXT PRIMARY KEY,
                ticker         TEXT NOT NULL,
                supplier       TEXT NOT NULL,
                category       TEXT NOT NULL,
                owner          TEXT NOT NULL,
                start_date     TEXT NOT NULL,
                expiry_date    TEXT NOT NULL,
                status         TEXT NOT NULL,
                annual_value_k INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_contracts_scope ON contracts (ticker, owner, expiry_date);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not csv_path.exists():
            log.warning("contracts CSV not found: %s", csv_path)
        else:
            digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            row = conn.execute("SELECT value FROM meta WHERE key='csv_sha256'").fetchone()
            if row is None or row["value"] != digest:
                with open(csv_path, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    missing = set(_COLUMNS) - set(reader.fieldnames or [])
                    if missing:
                        raise ValueError(f"{csv_path.name} is missing columns: {sorted(missing)}")
                    rows = [(r["contract_id"], r["ticker"].upper(), r["supplier"], r["category"],
                             r["owner"], r["start_date"], r["expiry_date"], r["status"],
                             int(r["annual_value_k"])) for r in reader]
                conn.execute("DELETE FROM contracts")
                conn.executemany("INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?,?)", rows)
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('csv_sha256', ?)", (digest,))
        return conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Filters:
    """User-chosen narrowing.  Never widens the permission scope."""
    ticker: str | None = None
    status: str | None = "Active"            # None = any status
    expiry_from: date | None = None
    expiry_to: date | None = None
    category: str | None = None
    owner: str | None = None


def _opt_str(body: dict, key: str) -> str | None:
    v = body.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str) or len(v) > 100:
        raise ValueError(f"{key} must be a short string.")
    return v.strip() or None


def _opt_date(body: dict, key: str) -> date | None:
    v = _opt_str(body, key)
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{key} must be a date like 2026-12-31.") from None


def parse_filters(body: dict) -> Filters:
    """Validate a request body into Filters.  Raises ValueError with a user-facing message."""
    status_raw = _opt_str(body, "status") or "Active"
    if status_raw.lower() == "all":
        status = None
    else:
        match = [s for s in STATUSES if s.lower() == status_raw.lower()]
        if not match:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}, or all.")
        status = match[0]

    quarter = _opt_str(body, "quarter")
    expiry_from, expiry_to = _opt_date(body, "expiry_from"), _opt_date(body, "expiry_to")
    if quarter and quarter.lower() not in ("all",):
        if expiry_from or expiry_to:
            raise ValueError("Use either quarter or expiry_from/expiry_to, not both.")
        expiry_from, expiry_to = quarter_bounds(quarter_of(AS_OF) if quarter.lower() == "current"
                                                else quarter.upper())
    if expiry_from and expiry_to and expiry_from > expiry_to:
        raise ValueError("expiry_from must not be after expiry_to.")

    ticker = _opt_str(body, "ticker")
    return Filters(ticker=ticker.upper() if ticker else None, status=status,
                   expiry_from=expiry_from, expiry_to=expiry_to,
                   category=_opt_str(body, "category"), owner=_opt_str(body, "owner"))


# ---------------------------------------------------------------------------
# Queries (permission scope is mandatory)
# ---------------------------------------------------------------------------

def _in(col: str, values: Iterable[str]) -> tuple[str, list[str]]:
    vals = sorted(set(values))
    return f"{col} IN ({','.join('?' * len(vals))})", vals


def fetch_contracts(tickers: Iterable[str], owners: Iterable[str] | None,
                    f: Filters = Filters()) -> list[dict[str, Any]]:
    """Contracts inside the (tickers, owners) scope that also match *f*."""
    tickers = {t.upper() for t in tickers}
    owners = None if owners is None else set(owners)
    if not tickers or (owners is not None and not owners):
        return []

    clause, params = _in("ticker", tickers)
    where = [clause]
    if owners is not None:
        clause, vals = _in("owner", owners)
        where.append(clause)
        params += vals
    for col, val in (("ticker", f.ticker), ("status", f.status), ("category", f.category),
                     ("owner", f.owner)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    if f.expiry_from:
        where.append("expiry_date >= ?")
        params.append(f.expiry_from.isoformat())
    if f.expiry_to:
        where.append("expiry_date <= ?")
        params.append(f.expiry_to.isoformat())

    sql = (f"SELECT {', '.join(_COLUMNS)} FROM contracts WHERE {' AND '.join(where)} "
           "ORDER BY expiry_date, annual_value_k DESC, contract_id")
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    for r in rows:
        r["days_to_expiry"] = (date.fromisoformat(r["expiry_date"]) - AS_OF).days
    return rows


def scope_options(tickers: Iterable[str], owners: Iterable[str] | None) -> dict[str, Any]:
    """The values a user can filter on, drawn only from rows inside their scope."""
    rows = fetch_contracts(tickers, owners, Filters(status=None))
    quarters = sorted({quarter_of(date.fromisoformat(r["expiry_date"])) for r in rows},
                      key=lambda q: (int(q[3:]), int(q[1])))
    return {
        "quarters": [{"value": q, "label": quarter_label(q)} for q in quarters],
        "categories": sorted({r["category"] for r in rows}),
        "owners": sorted({r["owner"] for r in rows}),
        "tickers": sorted({r["ticker"] for r in rows}),
        "statuses": list(STATUSES),
        "expiry_min": min((r["expiry_date"] for r in rows), default=None),
        "expiry_max": max((r["expiry_date"] for r in rows), default=None),
    }


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _pct(part: float, whole: float) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _group(rows: list[dict], key: str) -> list[dict]:
    agg: dict[str, list[int]] = {}
    for r in rows:
        a = agg.setdefault(r[key], [0, 0])
        a[0] += 1
        a[1] += r["annual_value_k"]
    total = sum(v for _, v in agg.values())
    out = [{"name": k, "contracts": c, "value_k": v, "share_pct": _pct(v, total)}
           for k, (c, v) in agg.items()]
    return sorted(out, key=lambda g: (-g["value_k"], g["name"]))


_BUCKETS = (("Already passed", None, -1), ("0–30 days", 0, 30), ("31–60 days", 31, 60),
            ("61–90 days", 61, 90), ("91–180 days", 91, 180), ("181+ days", 181, None))


def _buckets(rows: list[dict]) -> list[dict]:
    out = []
    for label, lo, hi in _BUCKETS:
        sel = [r for r in rows if (lo is None or r["days_to_expiry"] >= lo)
               and (hi is None or r["days_to_expiry"] <= hi)]
        out.append({"label": label, "contracts": len(sel),
                    "value_k": sum(r["annual_value_k"] for r in sel)})
    return out


def _timeline(rows: list[dict], f: Filters, lo: str | None, hi: str | None) -> dict:
    """Contracts and value per month (window <= 12 months) or per quarter, zero-filled."""
    if not rows or not lo or not hi:
        return {"unit": "month", "points": []}
    start = max(f.expiry_from or date.fromisoformat(lo), date.fromisoformat(lo))
    end = min(f.expiry_to or date.fromisoformat(hi), date.fromisoformat(hi))
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    unit = "month" if months <= 12 else "quarter"

    keys: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        keys.append((y, m) if unit == "month" else (y, (m - 1) // 3 + 1))
        m += 1 if unit == "month" else 3
        if m > 12:
            y, m = y + 1, m - 12
    keys = list(dict.fromkeys(keys))

    def key_of(d: date) -> tuple[int, int]:
        return (d.year, d.month) if unit == "month" else (d.year, (d.month - 1) // 3 + 1)

    totals = {k: [0, 0] for k in keys}
    for r in rows:
        k = key_of(date.fromisoformat(r["expiry_date"]))
        if k in totals:
            totals[k][0] += 1
            totals[k][1] += r["annual_value_k"]
    names = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())
    return {"unit": unit, "points": [
        {"label": f"{names[k[1] - 1]} {k[0]}" if unit == "month" else f"Q{k[1]} {k[0]}",
         "contracts": c, "value_k": v} for k, (c, v) in totals.items()]}


def analyze(tickers: Iterable[str], owners: Iterable[str] | None, f: Filters) -> dict[str, Any]:
    """Expiry-exposure statistics for the contracts matching *f* inside the permission scope."""
    tickers = {t.upper() for t in tickers}
    owners = None if owners is None else set(owners)

    rows = fetch_contracts(tickers, owners, f)
    # Denominator for "share of active spend": ALL active contracts for the same
    # company/category/owner selection, whatever their expiry date.
    active_all = fetch_contracts(tickers, owners, replace(f, status="Active",
                                                          expiry_from=None, expiry_to=None))
    options = scope_options(tickers, owners)

    value = sum(r["annual_value_k"] for r in rows)
    active_value = sum(r["annual_value_k"] for r in active_all)
    suppliers = _group(rows, "supplier")
    soon = [r for r in rows if 0 <= r["days_to_expiry"] <= 30]
    upcoming = [r["days_to_expiry"] for r in rows if r["days_to_expiry"] >= 0]

    return {
        "as_of": AS_OF.isoformat(),
        "filters": {
            "ticker": f.ticker, "status": f.status or "all",
            "quarter": (quarter_of(f.expiry_from)
                        if f.expiry_from and f.expiry_to
                        and quarter_bounds(quarter_of(f.expiry_from)) == (f.expiry_from, f.expiry_to)
                        else None),
            "expiry_from": f.expiry_from.isoformat() if f.expiry_from else None,
            "expiry_to": f.expiry_to.isoformat() if f.expiry_to else None,
            "category": f.category, "owner": f.owner,
        },
        "kpis": {
            "contracts": len(rows),
            "value_k": value,
            "active_spend_k": active_value,
            # Only meaningful when looking at Active contracts: expiring value / all active value.
            "exposure_pct": _pct(value, active_value) if f.status == "Active" else None,
            "top_supplier": suppliers[0] if suppliers else None,
            "top3_share_pct": _pct(sum(s["value_k"] for s in suppliers[:3]), value),
            "expiring_30d": {"contracts": len(soon), "value_k": sum(r["annual_value_k"] for r in soon)},
            "avg_days_to_expiry": round(sum(upcoming) / len(upcoming)) if upcoming else None,
        },
        "timeline": _timeline(rows, f, options["expiry_min"], options["expiry_max"]),
        "by_category": _group(rows, "category"),
        "by_owner": _group(rows, "owner"),
        "by_ticker": _group(rows, "ticker"),
        "top_suppliers": suppliers[:5],
        "buckets": _buckets(rows),
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "options": options,
    }
