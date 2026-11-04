"""
financials.py — quarterly financial trend analytics.

Reads the two CSVs written by ``tools/extract_financials.py`` (which in turn come
from the quarterly-report PDFs) and computes growth, margin, trend and
comparison statistics.  Everything is plain Python on a few dozen rows, so there
is no database: the CSVs are loaded once and reloaded when they change.

Scope is a set of tickers, applied before any calculation.  A caller can never
see, or be compared against, a company outside their scope.

All amounts are $ millions (EPS in $).  Periods are "Qn-YYYY".
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

_FIN_PATH: Path | None = None
_SEG_PATH: Path | None = None
_STAMP: tuple | None = None
_FIN: dict[str, list[dict]] = {}          # ticker -> rows oldest -> newest
_SEG: dict[tuple, dict[str, float]] = {}  # (ticker, period) -> {segment: revenue}

_NUMERIC = ("revenue", "cost_of_revenue", "gross_profit", "rd", "sga", "operating_income",
            "net_interest", "pretax_income", "income_tax", "net_income", "diluted_shares",
            "eps", "cash", "total_debt", "equity", "operating_cash_flow", "capex",
            "free_cash_flow", "shareholder_returns", "d_and_a", "sbc")

METRICS = {          # key -> (label, unit)  — what the trend chart can plot
    "revenue":          ("Revenue", "$M"),
    "gross_margin":     ("Gross margin", "%"),
    "operating_margin": ("Operating margin", "%"),
    "net_margin":       ("Net margin", "%"),
    "net_income":       ("Net income", "$M"),
    "eps":              ("Diluted EPS", "$"),
    "free_cash_flow":   ("Free cash flow", "$M"),
    "fcf_margin":       ("FCF margin", "%"),
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _stamp(*paths: Path) -> tuple:
    return tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)


def init_financials(financials_csv: str | Path, segments_csv: str | Path) -> None:
    global _FIN_PATH, _SEG_PATH
    _FIN_PATH, _SEG_PATH = Path(financials_csv), Path(segments_csv)
    _load(force=True)


def _load(force: bool = False) -> None:
    global _STAMP
    if _FIN_PATH is None or _SEG_PATH is None:
        raise RuntimeError("financials.init_financials() has not been called")
    stamp = _stamp(_FIN_PATH, _SEG_PATH)
    if not force and stamp == _STAMP:
        return
    fin: dict[str, list[dict]] = {}
    with _FIN_PATH.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            row = {"ticker": raw["ticker"], "period": raw["period"],
                   "year": int(raw["year"]), "quarter": int(raw["quarter"])}
            for k in _NUMERIC:
                row[k] = float(raw[k]) if raw.get(k) not in (None, "") else None
            fin.setdefault(row["ticker"], []).append(row)
    for rows in fin.values():
        rows.sort(key=lambda r: (r["year"], r["quarter"]))
    seg: dict[tuple, dict[str, float]] = {}
    with _SEG_PATH.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            seg.setdefault((raw["ticker"], raw["period"]), {})[raw["segment"]] = float(raw["revenue"])
    _FIN.clear(); _FIN.update(fin)
    _SEG.clear(); _SEG.update(seg)
    _STAMP = stamp


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

@dataclass
class Filters:
    ticker: str | None = None          # None = every company in scope
    from_period: str | None = None     # "Q3-2024"
    to_period: str | None = None
    metric: str = "revenue"


def _pkey(period: str) -> tuple[int, int]:
    q, y = period.split("-")
    return int(y), int(q[1])


def _parse_period(value) -> str | None:
    if value in (None, "", "all"):
        return None
    s = str(value).strip().upper().replace(" ", "-")
    if len(s) == 7 and s[0] == "Q" and s[1] in "1234" and s[2] == "-" and s[3:].isdigit():
        return s
    raise ValueError("Periods look like Q2-2026.")


def parse_filters(body: dict) -> Filters:
    f = Filters()
    t = (body.get("ticker") or "").strip().upper()
    f.ticker = t or None
    f.from_period = _parse_period(body.get("from"))
    f.to_period = _parse_period(body.get("to"))
    if f.from_period and f.to_period and _pkey(f.from_period) > _pkey(f.to_period):
        raise ValueError("'from' must not be after 'to'.")
    metric = (body.get("metric") or "revenue").strip()
    if metric not in METRICS:
        raise ValueError(f"Unknown metric. Choose one of: {', '.join(METRICS)}.")
    f.metric = metric
    return f


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _pct(new, old):
    """Percentage change, or None when it is undefined."""
    if new is None or old in (None, 0):
        return None
    return round(100.0 * (new - old) / abs(old), 2)


def _ratio(a, b):
    if a is None or not b:
        return None
    return round(100.0 * a / b, 2)


def _linreg(ys: list[float]) -> tuple[float, float]:
    """Least-squares slope per step and R² of ys against 0..n-1."""
    n = len(ys)
    if n < 3:
        return 0.0, 0.0
    xs = range(n)
    mx, my = (n - 1) / 2, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    return slope, (sxy * sxy) / (sxx * syy)


def _derive(rows: list[dict]) -> list[dict]:
    """Add margins, growth rates and trailing-four-quarter sums to each row."""
    out = []
    for i, r in enumerate(rows):
        d = dict(r)
        d["gross_margin"]     = _ratio(r["gross_profit"], r["revenue"])
        d["operating_margin"] = _ratio(r["operating_income"], r["revenue"])
        d["net_margin"]       = _ratio(r["net_income"], r["revenue"])
        d["fcf_margin"]       = _ratio(r["free_cash_flow"], r["revenue"])
        prev = rows[i - 1] if i >= 1 else None
        year = rows[i - 4] if i >= 4 else None
        for key in ("revenue", "operating_income", "net_income", "eps", "free_cash_flow"):
            d[f"{key}_qoq"] = _pct(r[key], prev[key]) if prev else None
            d[f"{key}_yoy"] = _pct(r[key], year[key]) if year else None
        if i >= 3:
            win = rows[i - 3:i + 1]
            d["ttm_revenue"]    = round(sum(x["revenue"] for x in win), 1)
            d["ttm_net_income"] = round(sum(x["net_income"] for x in win), 1)
            d["ttm_fcf"]        = round(sum(x["free_cash_flow"] for x in win), 1)
        else:
            d["ttm_revenue"] = d["ttm_net_income"] = d["ttm_fcf"] = None
        out.append(d)
    return out


def _seasonality(rows: list[dict]) -> dict[str, float] | None:
    """Each calendar quarter's revenue relative to its own year's average (1.00 = average)."""
    by_year: dict[int, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r)
    ratios: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    for yr in by_year.values():
        if len(yr) != 4:                      # only complete calendar years are comparable
            continue
        avg = sum(x["revenue"] for x in yr) / 4
        for x in yr:
            ratios[x["quarter"]].append(x["revenue"] / avg)
    if not any(ratios.values()):
        return None
    return {f"Q{q}": round(sum(v) / len(v), 3) for q, v in ratios.items() if v}


def _company_stats(rows: list[dict]) -> dict:
    revs = [r["revenue"] for r in rows]
    slope, r2 = _linreg(revs)
    qoq = [r["revenue_qoq"] for r in rows if r["revenue_qoq"] is not None]
    last = rows[-1]
    cagr = None
    if len(rows) >= 12:                       # two-year CAGR of trailing-four-quarter revenue
        a, b = rows[-9]["ttm_revenue"], rows[-1]["ttm_revenue"]
        if a and b:
            cagr = round(100 * ((b / a) ** 0.5 - 1), 2)
    return {
        "trend_slope_per_qtr": round(slope, 2),
        "trend_r2": round(r2, 3),
        "qoq_mean_pct": round(statistics.fmean(qoq), 2) if qoq else None,
        "qoq_stdev_pct": round(statistics.stdev(qoq), 2) if len(qoq) > 1 else None,
        "revenue_cagr_2y_pct": cagr,
        "seasonality": _seasonality(rows),
        "quarters": len(rows),
        "latest_period": last["period"],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scope_periods(tickers: set[str]) -> list[str]:
    """Every period available to this scope, oldest -> newest."""
    _load()
    seen = {r["period"] for t in tickers for r in _FIN.get(t, [])}
    return sorted(seen, key=_pkey)


def _row_out(d: dict) -> dict:
    keep = ("period", "revenue", "gross_profit", "operating_income", "net_income", "eps",
            "free_cash_flow", "cash", "total_debt", "gross_margin", "operating_margin",
            "net_margin", "fcf_margin", "revenue_qoq", "revenue_yoy", "operating_income_yoy",
            "net_income_yoy", "eps_yoy", "ttm_revenue", "ttm_net_income", "ttm_fcf")
    return {k: d.get(k) for k in keep}


def analyze(tickers: set[str], f: Filters) -> dict:
    """
    Growth, margins and comparisons for the companies in *tickers* (the caller's scope),
    optionally narrowed to one company and a period window.

    Growth rates are computed on the full history and then windowed, so the first
    quarters shown still carry their QoQ/YoY values whenever the data exists.
    """
    _load()
    picked = sorted(t for t in tickers if (f.ticker is None or t == f.ticker) and t in _FIN)
    lo = _pkey(f.from_period) if f.from_period else (0, 0)
    hi = _pkey(f.to_period) if f.to_period else (9999, 9)

    companies = []
    for tk in picked:
        full = _derive(_FIN[tk])
        window = [d for d in full if lo <= (d["year"], d["quarter"]) <= hi]
        if not window:
            continue
        stats = _company_stats(window)
        last = window[-1]
        first = window[0]
        yoy_base = next((d for d in full if (d["year"], d["quarter"]) == (last["year"] - 1, last["quarter"])), None)
        latest_segments = _SEG.get((tk, last["period"]), {})
        prior_segments = _SEG.get((tk, yoy_base["period"]), {}) if yoy_base else {}
        seg_total = sum(latest_segments.values()) or None
        segments = [
            {"name": name, "revenue": rev,
             "share_pct": _ratio(rev, seg_total),
             "yoy_pct": _pct(rev, prior_segments.get(name)) if prior_segments else None}
            for name, rev in sorted(latest_segments.items(), key=lambda kv: -kv[1])
        ]
        companies.append({
            "ticker": tk,
            "latest": _row_out(last),
            "margin_change_bps_yoy": {
                k: (round((last[k] - yoy_base[k]) * 100) if yoy_base and last[k] is not None and yoy_base[k] is not None else None)
                for k in ("gross_margin", "operating_margin", "net_margin")
            },
            "stats": stats,
            "series": [_row_out(d) for d in window],
            "indexed": [{"period": d["period"],
                         "value": round(100.0 * d["revenue"] / first["revenue"], 1)} for d in window],
            "segments": segments,
        })

    periods = sorted({r["period"] for c in companies for r in c["series"]}, key=_pkey)
    return {
        "filters": {"ticker": f.ticker, "from": f.from_period, "to": f.to_period, "metric": f.metric},
        "metric": {"key": f.metric, "label": METRICS[f.metric][0], "unit": METRICS[f.metric][1]},
        "metrics": {k: {"label": v[0], "unit": v[1]} for k, v in METRICS.items()},
        "periods": periods,
        "companies": companies,
        "ranking": _ranking(companies),
        "as_of": periods[-1] if periods else None,
    }


def _ranking(companies: list[dict]) -> list[dict]:
    """Latest-quarter league table: who leads on each measure (needs two or more companies)."""
    if len(companies) < 2:
        return []
    measures = (("revenue_yoy", "Revenue growth YoY", "%"), ("gross_margin", "Gross margin", "%"),
                ("operating_margin", "Operating margin", "%"), ("net_margin", "Net margin", "%"),
                ("fcf_margin", "FCF margin", "%"), ("eps_yoy", "EPS growth YoY", "%"))
    out = []
    for key, label, unit in measures:
        vals = [(c["ticker"], c["latest"].get(key)) for c in companies if c["latest"].get(key) is not None]
        if len(vals) < 2:
            continue
        vals.sort(key=lambda kv: -kv[1])
        out.append({"measure": label, "unit": unit,
                    "order": [{"ticker": t, "value": v} for t, v in vals]})
    return out
