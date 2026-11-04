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

from . import behavior as _beh      # the improved / worse / flat rule is shared with customer behavior

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

ASSESSMENTS = ("all", "improved", "worsened", "flat")
BASES = {"yoy": "the same quarter last year", "qoq": "the previous quarter"}
LAG = {"yoy": 4, "qoq": 1}          # rows back from the latest quarter

# Measures given an improved / worse / flat flag.  kind "pts" = a ratio compared in percentage
# points; "pct" = an amount compared by percentage change.  The thresholds are the ones the
# customer-behavior screen uses (behavior.RATE_PTS / RATE_REL_PCT / OTHER_PCT).
SCORED = (   # key, label, group, kind, direction, unit
    ("revenue",             "Revenue",                  "Growth",        "pct", "up",   "$M"),
    ("gross_profit",        "Gross profit",             "Growth",        "pct", "up",   "$M"),
    ("operating_income",    "Operating income",         "Growth",        "pct", "up",   "$M"),
    ("net_income",          "Net income",               "Growth",        "pct", "up",   "$M"),
    ("eps",                 "Diluted EPS",              "Growth",        "pct", "up",   "$"),
    ("gross_margin",        "Gross margin",             "Margins",       "pts", "up",   "%"),
    ("operating_margin",    "Operating margin",         "Margins",       "pts", "up",   "%"),
    ("net_margin",          "Net margin",               "Margins",       "pts", "up",   "%"),
    ("sga_pct",             "SG&A as % of revenue",     "Costs",         "pts", "down", "%"),
    ("operating_cash_flow", "Operating cash flow",      "Cash",          "pct", "up",   "$M"),
    ("free_cash_flow",      "Free cash flow",           "Cash",          "pct", "up",   "$M"),
    ("fcf_margin",          "FCF margin",               "Cash",          "pts", "up",   "%"),
    ("cash",                "Cash balance",             "Balance sheet", "pct", "up",   "$M"),
    ("total_debt",          "Total debt",               "Balance sheet", "pct", "down", "$M"),
    ("equity",              "Equity",                   "Balance sheet", "pct", "up",   "$M"),
)


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
    assessment: str = "all"            # all | improved | worsened | flat  (narrows the flagged measures)
    basis: str = "yoy"                 # yoy | qoq  (what the flags compare the latest quarter with)


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
    a = (body.get("assessment") or "all").strip().lower()
    if a not in ASSESSMENTS:
        raise ValueError(f"assessment must be one of {', '.join(ASSESSMENTS)}.")
    f.assessment = a
    b = (body.get("basis") or "yoy").strip().lower()
    if b not in BASES:
        raise ValueError(f"basis must be one of {', '.join(BASES)}.")
    f.basis = b
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
        d["sga_pct"]          = _ratio(abs(r["sga"]), r["revenue"]) if r["sga"] is not None else None
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
# Improved / worse / flat flags
# ---------------------------------------------------------------------------

def _flag(key, label, group, kind, direction, unit, new, old) -> dict:
    """One measure compared with an earlier quarter, with its improved / worsened / flat verdict."""
    if kind == "pts":
        change = round(new - old, 2) if new is not None and old is not None else None
        rel = _pct(new, old)
    else:
        change = rel = _pct(new, old)
    verdict = _beh._assess(direction, change, kind, rel)
    return {"key": key, "label": label, "group": group, "unit": unit, "kind": kind, "direction": direction,
            "value": new, "prior": old, "change": change, "rel_pct": rel, "assessment": verdict,
            "why": _beh._why(direction, change, kind, rel, verdict) if verdict else None}


def _flags(ticker: str, full: list[dict], i: int, basis: str) -> list[dict]:
    """Flags for quarter ``full[i]`` against ``LAG[basis]`` quarters earlier; [] when there is no such quarter."""
    j = i - LAG[basis]
    if j < 0:
        return []
    cur, old = full[i], full[j]
    out = [_flag(k, lab, grp, kind, dr, unit, cur.get(k), old.get(k)) for k, lab, grp, kind, dr, unit in SCORED]
    seg_now, seg_old = _SEG.get((ticker, cur["period"]), {}), _SEG.get((ticker, old["period"]), {})
    for name in seg_now:                      # a segment is only compared when it existed in both quarters
        if name in seg_old:
            out.append(_flag("segment:" + name, f"{name} revenue", "Segments", "pct", "up", "$M", seg_now[name], seg_old[name]))
    return [m for m in out if m["assessment"]]


def _tally(measures: list[dict]) -> dict:
    t = {a: sum(m["assessment"] == a for m in measures) for a in ("improved", "worsened", "flat")}
    t["tracked"] = sum(t.values())
    return t


def _scorecard(ticker: str, full: list[dict], window: list[dict], f: Filters) -> dict | None:
    """Flags for the latest quarter in the window, plus how the tally has moved quarter by quarter."""
    pos = {d["period"]: n for n, d in enumerate(full)}
    i = pos[window[-1]["period"]]
    measures = _flags(ticker, full, i, f.basis)
    if not measures:
        return None
    history = []
    for d in window:
        m = _flags(ticker, full, pos[d["period"]], f.basis)
        if m:
            history.append({"period": d["period"], **_tally(m)})

    def top(which: str) -> list[dict]:
        pool = [m for m in measures if m["assessment"] == which and m["rel_pct"] is not None]
        return sorted(pool, key=lambda m: -abs(m["rel_pct"]))[:3]

    shown = [m for m in measures if f.assessment in ("all", m["assessment"])]
    return {"basis": f.basis, "basis_label": BASES[f.basis], "period": full[i]["period"],
            "prior_period": full[i - LAG[f.basis]]["period"], **_tally(measures),
            "concerns": top("worsened"), "improved_top": top("improved"),
            "measures": shown, "history": history,
            "rule": {"rate_pts": _beh.RATE_PTS, "rate_rel_pct": _beh.RATE_REL_PCT, "other_pct": _beh.OTHER_PCT}}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Next-quarter estimate
# ---------------------------------------------------------------------------

FORECAST_MEASURES = (("revenue", "Revenue", "$M"), ("operating_income", "Operating income", "$M"),
                     ("net_income", "Net income", "$M"), ("eps", "Diluted EPS", "$"),
                     ("free_cash_flow", "Free cash flow", "$M"))
FORECAST_AVG = 3          # year-on-year changes averaged to carry the trend forward
FORECAST_MIN_TESTS = 3    # earlier quarters the method must have been tested on before it is shown
RANGE_Z = 1.28            # about 80% of past misses fell inside a band of this many typical misses


def _next_period(period: str) -> str:
    y, q = _pkey(period)
    return f"Q{q % 4 + 1}-{y + (1 if q == 4 else 0)}"


def _estimate(vals: list[float], j: int) -> float | None:
    """Estimate vals[j] from earlier values only: the same quarter a year before, plus the
    average year-on-year change of the last few quarters (which keeps the seasonal shape)."""
    if j < FORECAST_AVG + 4:
        return None
    deltas = [vals[j - k] - vals[j - k - 4] for k in range(1, FORECAST_AVG + 1)]
    return vals[j - 4] + sum(deltas) / FORECAST_AVG


SURPRISE_MIN_PCT = 3.0    # a result must also be this far (as % of the estimate) from the estimate to count


def _surprises(rows: list[dict]) -> list[dict]:
    """Measures whose latest quarter landed outside the range the estimate method would have
    given from the history before it.  Needs the same testing history as the forecast."""
    n = len(rows)
    if n < FORECAST_AVG + 4 + FORECAST_MIN_TESTS + 1:
        return []
    out = []
    for key, label, unit in FORECAST_MEASURES:
        vals = [r[key] for r in rows]
        if any(v is None for v in vals):
            continue
        point = _estimate(vals, n - 1)
        misses = [vals[j] - _estimate(vals, j) for j in range(FORECAST_AVG + 4, n - 1)]
        band = RANGE_Z * (sum(m * m for m in misses) / len(misses)) ** 0.5
        miss = vals[-1] - point
        if abs(miss) <= band or not point or abs(100 * miss / point) < SURPRISE_MIN_PCT:
            continue
        dec = 2 if unit == "$" else 1
        out.append({"key": key, "label": label, "unit": unit, "period": rows[-1]["period"],
                    "actual": round(vals[-1], dec), "expected": round(point, dec),
                    "low": round(point - band, dec), "high": round(point + band, dec),
                    "miss_pct": round(100 * miss / abs(point), 1), "direction": "above" if miss > 0 else "below"})
    out.sort(key=lambda m: -abs(m["miss_pct"]))
    return out


def _forecast(rows: list[dict]) -> dict | None:
    """Next-quarter estimate with a range, from the history up to the last quarter in view.

    The range comes from testing the same method on every earlier quarter it could have
    been applied to: the band is RANGE_Z times the root-mean-square of those misses.  It is
    an estimate, not guidance, and it assumes next quarter looks like the recent past."""
    n = len(rows)
    if n < FORECAST_AVG + 4 + FORECAST_MIN_TESTS:
        return None
    measures = []
    for key, label, unit in FORECAST_MEASURES:
        vals = [r[key] for r in rows]
        if any(v is None for v in vals):
            continue
        point = _estimate(vals, n)
        misses = [vals[j] - _estimate(vals, j) for j in range(FORECAST_AVG + 4, n)]
        rmse = (sum(m * m for m in misses) / len(misses)) ** 0.5
        band = RANGE_Z * rmse
        dec = 2 if unit == "$" else 1
        year_ago = vals[n - 4]
        measures.append({
            "key": key, "label": label, "unit": unit,
            "estimate": round(point, dec), "low": round(point - band, dec), "high": round(point + band, dec),
            "last": round(vals[-1], dec), "year_ago": round(year_ago, dec),
            "vs_year_ago_pct": _pct(point, year_ago),
            "typical_miss_pct": round(100 * rmse / abs(point), 1) if point else None,
        })
    if not measures:
        return None
    return {"period": _next_period(rows[-1]["period"]), "based_on": rows[-1]["period"], "quarters_used": n,
            "tested_on": n - FORECAST_AVG - 4, "measures": measures,
            "method": (f"Same quarter a year earlier plus the average of the last {FORECAST_AVG} year-on-year changes. "
                       f"The range is the {int(100 * 0.8)}% band from testing that method on {n - FORECAST_AVG - 4} earlier quarters."),
            "caution": "A statistical estimate from past reports, not company guidance or a prediction of events."}


def report_figures(ticker: str, period: str) -> dict | None:
    """Key figures and flags for one reported quarter (against the same quarter a year earlier).

    Used beside a search hit on that quarter's report.  The caller has already limited the hit to a
    company the account may see; this adds nothing beyond that company's own numbers."""
    _load()
    full = _derive(_FIN.get(ticker, []))
    i = next((n for n, d in enumerate(full) if d["period"] == period), None)
    if i is None:
        return None
    d = full[i]
    flags = _flags(ticker, full, i, "yoy")
    t = _tally(flags) if flags else None
    concerns = sorted((m for m in flags if m["assessment"] == "worsened" and m["rel_pct"] is not None),
                      key=lambda m: -abs(m["rel_pct"]))[:3]
    return {"kind": "quarter", "period": period, "revenue": d["revenue"], "revenue_yoy": d["revenue_yoy"],
            "gross_margin": d["gross_margin"], "operating_margin": d["operating_margin"],
            "net_income": d["net_income"], "eps": d["eps"], "free_cash_flow": d["free_cash_flow"],
            "improved": t["improved"] if t else None, "worsened": t["worsened"] if t else None,
            "flat": t["flat"] if t else None, "concerns": [m["label"] for m in concerns]}


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
        upto = [d for d in full if (d["year"], d["quarter"]) <= (last["year"], last["quarter"])]
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
            "scorecard": _scorecard(tk, full, window, f),
            "forecast": _forecast(upto),
            "surprises": _surprises(upto),
        })

    periods = sorted({r["period"] for c in companies for r in c["series"]}, key=_pkey)
    return {
        "filters": {"ticker": f.ticker, "from": f.from_period, "to": f.to_period, "metric": f.metric,
                    "assessment": f.assessment, "basis": f.basis},
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
