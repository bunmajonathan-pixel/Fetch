"""
behavior.py — customer-behavior analytics.

Reads ``data/behavior/behavior.csv`` (written by ``tools/extract_behavior.py`` from the
customer-behavior report PDFs) and turns it into a year-over-year scorecard per company:
what improved, what got worse, where growth came from, and how the customer mix moved.

Scope is a set of tickers, applied before any calculation.  Peer comparison only ever
uses companies inside that scope.

The three companies measure different things (households for a food company, accounts
and logos for the B2B ones), so metrics are never forced into a common schema.  Only
measures that mean the same thing for every company are compared across companies.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

_PATH: Path | None = None
_STAMP: tuple | None = None
_ROWS: dict[str, dict[int, list[dict]]] = {}      # ticker -> year -> rows in report order

# A change is "meaningful" when a rate moves 1 percentage point, or by 5% of its own level
# (so 8.5% churn falling to 7.7% counts even though it is under a point), and when any
# other measure moves 2%.  Smaller moves are shown as "flat" so noise is not reported
# as progress or decline.
RATE_PTS = 1.0
RATE_REL_PCT = 5.0
OTHER_PCT = 2.0

ASSESSMENTS = ("all", "improved", "worsened", "flat")
NPS_KEYS = ("net_promoter_score", "brand_net_promoter_score")


def init_behavior(csv_path: str | Path) -> None:
    global _PATH
    _PATH = Path(csv_path)
    _load(force=True)


def _load(force: bool = False) -> None:
    global _STAMP
    if _PATH is None:
        raise RuntimeError("behavior.init_behavior() has not been called")
    st = _PATH.stat()
    stamp = (st.st_mtime_ns, st.st_size)
    if not force and stamp == _STAMP:
        return
    rows: dict[str, dict[int, list[dict]]] = {}
    with _PATH.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            r = dict(raw)
            r["year"] = int(raw["year"])
            r["value"] = float(raw["value"])
            r["printed_change"] = float(raw["printed_change"]) if raw["printed_change"] != "" else None
            rows.setdefault(r["ticker"], {}).setdefault(r["year"], []).append(r)
    _ROWS.clear()
    _ROWS.update(rows)
    _STAMP = stamp


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

@dataclass
class Filters:
    ticker: str | None = None
    year: int | None = None            # None = latest year in scope
    assessment: str = "all"


def scope_years(tickers: set[str]) -> list[int]:
    _load()
    return sorted({y for t in tickers for y in _ROWS.get(t, {})})


def parse_filters(body: dict) -> Filters:
    _load()
    f = Filters()
    t = (body.get("ticker") or "").strip().upper()
    f.ticker = t or None
    y = body.get("year")
    if y not in (None, "", "latest", "all"):
        try:
            f.year = int(y)
        except (TypeError, ValueError):
            raise ValueError("year must be a four-digit year.") from None
        known = sorted({yy for by in _ROWS.values() for yy in by})
        if f.year not in known:
            raise ValueError(f"No behavior data for {f.year}. Available years: {', '.join(map(str, known))}.")
    a = (body.get("assessment") or "all").strip().lower()
    if a not in ASSESSMENTS:
        raise ValueError(f"assessment must be one of {', '.join(ASSESSMENTS)}.")
    f.assessment = a
    return f


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def _rel(new: float, old: float) -> float | None:
    """Relative change in percent, or None when undefined."""
    if not old:
        return None
    return round(100.0 * (new / old - 1), 1)


def _assess(direction: str, change: float | None, kind: str | None, rel: float | None = None) -> str | None:
    """improved | worsened | flat | None (neutral, or nothing to compare with)."""
    if change is None or direction == "neutral":
        return None
    if kind == "pts":
        meaningful = abs(change) >= RATE_PTS or (rel is not None and abs(rel) >= RATE_REL_PCT)
    else:
        meaningful = abs(change) >= OTHER_PCT
    if not meaningful:
        return "flat"
    return "improved" if (change > 0) == (direction == "up") else "worsened"


def _why(direction: str, change: float, kind: str | None, rel: float | None, assessment: str) -> str:
    """One sentence saying why a measure was put in its group."""
    if kind == "pts":
        moved = f"{abs(change):g} pts" + (f" ({rel:+.1f}% of its level)" if rel is not None else "")
        rule = f"under {RATE_PTS:g} pt and under {RATE_REL_PCT:g}% of its level" if assessment == "flat" \
            else f"at least {RATE_PTS:g} pt or {RATE_REL_PCT:g}% of its level"
    else:
        moved = f"{abs(change):g}%"
        rule = f"under {OTHER_PCT:g}%" if assessment == "flat" else f"at least {OTHER_PCT:g}%"
    if assessment == "flat":
        return f"Moved {moved}, {rule}, so it is shown as flat."
    good = "higher" if direction == "up" else "lower"
    went = "rose" if change > 0 else "fell"
    return f"{went.capitalize()} {moved}, {rule}. {good.capitalize()} is better, so this {assessment}."


def _row_out(r: dict, prior: dict | None) -> dict:
    out = {"section": r["section"], "key": r["key"], "label": r["label"], "value": r["value"],
           "unit": r["unit"], "direction": r["direction"], "kind": r["kind"],
           "prior": None, "change": None, "change_kind": None, "rel_pct": None, "assessment": None, "why": None}
    if prior is not None:
        # the printed change is what the report states; the extractor has checked it against both years
        out["prior"] = prior["value"]
        out["change"] = r["printed_change"]
        out["change_kind"] = r["change_kind"] or None
        out["rel_pct"] = _rel(r["value"], prior["value"])
        out["assessment"] = _assess(r["direction"], r["printed_change"], r["change_kind"], out["rel_pct"])
        if out["assessment"]:
            out["why"] = _why(r["direction"], r["printed_change"], r["change_kind"], out["rel_pct"], out["assessment"])
    return out


def _by_role(rows: list[dict], role: str) -> dict | None:
    return next((r for r in rows if r["role"] == role), None)


def _company(ticker: str, year: int, f: Filters) -> dict:
    cur = _ROWS[ticker][year]
    prev_rows = _ROWS[ticker].get(year - 1)
    prior_by = {(r["section"], r["key"]): r for r in prev_rows} if prev_rows else {}
    rows = [_row_out(r, prior_by.get((r["section"], r["key"])) if prev_rows else None) for r in cur]

    revenue = next(r for r in rows if r["key"] == "revenue")
    vol_row = _by_role(cur, "volume")
    vol = next(r for r in rows if r["key"] == vol_row["key"])
    growth = None
    if prev_rows:
        rg, vg = revenue["rel_pct"], vol["rel_pct"]
        growth = {"revenue_pct": rg, "customers_pct": vg,
                  "per_customer_pct": round(100 * ((1 + rg / 100) / (1 + vg / 100) - 1), 1)}

    scored = [r for r in rows if r["assessment"] in ("improved", "worsened", "flat") and r["key"] != "revenue"]
    score = {"improved": sum(r["assessment"] == "improved" for r in scored),
             "worsened": sum(r["assessment"] == "worsened" for r in scored),
             "flat": sum(r["assessment"] == "flat" for r in scored)}
    score["tracked"] = sum(score.values())

    def top(which: str) -> list[dict]:
        pool = [r for r in scored if r["assessment"] == which and r["rel_pct"] is not None]
        return sorted(pool, key=lambda r: -abs(r["rel_pct"]))[:3]

    for r in top("worsened"):
        peers = [x for x in scored if x["section"] == r["section"] and x is not r]
        tally = {a: sum(x["assessment"] == a for x in peers) for a in ("improved", "worsened", "flat")}
        r["context"] = (f"Other measures in {r['section']}: {tally['improved']} improved, {tally['worsened']} worse, "
                        f"{tally['flat']} flat. Revenue {'grew' if (revenue['rel_pct'] or 0) >= 0 else 'fell'} "
                        f"{abs(revenue['rel_pct'] or 0):g}% over the same year.") if prev_rows else None

    mix_moves = sorted((r for r in rows if r["kind"] == "mix" and r["change"] is not None and r["change"] != 0),
                       key=lambda r: -abs(r["change"]))[:4]

    gain, loss = _by_role(cur, "gain"), _by_role(cur, "loss")
    roll = None
    if gain and loss:
        start = vol_row["value"] - gain["value"] + loss["value"]
        roll = {"start": start, "gained": gain["value"], "lost": loss["value"], "end": vol_row["value"],
                "gained_label": gain["label"], "lost_label": loss["label"],
                "churn_pct": round(100 * loss["value"] / start, 1)}

    # sections in report order, optionally narrowed to one assessment
    sections: list[dict] = []
    for r in rows:
        if f.assessment != "all" and (r["assessment"] != f.assessment or r["key"] == "revenue"):
            continue
        if r["key"] == "revenue":
            continue                                    # shown in the header, not in a table
        if not sections or sections[-1]["name"] != r["section"]:
            sections.append({"name": r["section"], "kind": r["kind"], "rows": []})
        sections[-1]["rows"].append(r)

    return {"ticker": ticker, "year": year, "prior_year": year - 1 if prev_rows else None,
            "revenue": {"value": revenue["value"], "prior": revenue["prior"], "growth_pct": revenue["rel_pct"]},
            "customers": {"label": vol["label"], "value": vol["value"], "prior": vol["prior"],
                          "unit": vol["unit"], "growth_pct": vol["rel_pct"]},
            "growth": growth,
            "scorecard": score if prev_rows else None,
            "improved": top("improved") if prev_rows else [], "concerns": top("worsened") if prev_rows else [],
            "mix_moves": mix_moves, "rollforward": roll, "sections": sections, "_rows": rows}


def _comparison(companies: list[dict], year: int) -> list[dict]:
    """Measures that mean the same thing for every company.  None where a company does not report it."""
    def find(c, keys):
        return next((r for r in c["_rows"] if r["key"] in keys), None)

    out = []
    tickers = [c["ticker"] for c in companies]

    def add(measure, unit, better, getter):
        vals = {c["ticker"]: getter(c) for c in companies}
        present = {t: v for t, v in vals.items() if v is not None}
        best = None
        if len(present) > 1:
            best = (max if better == "up" else min)(present, key=present.get)
        out.append({"measure": measure, "unit": unit, "better": better, "values": vals, "best": best})

    add(f"Revenue growth, {year} vs. {year - 1}", "%", "up", lambda c: c["revenue"]["growth_pct"])
    add("Customer base growth", "%", "up", lambda c: c["customers"]["growth_pct"])
    add("Revenue per customer, change", "%", "up", lambda c: c["growth"]["per_customer_pct"] if c["growth"] else None)
    add("Net Promoter Score", "", "up", lambda c: (find(c, NPS_KEYS) or {}).get("value"))
    add("Net Promoter Score, change", " pts", "up",
        lambda c: (lambda r: None if not r or r["prior"] is None else round(r["value"] - r["prior"], 1))(find(c, NPS_KEYS)))
    add("Logo churn rate", "%", "down", lambda c: (find(c, ("logo_churn_rate",)) or {}).get("value"))
    return [m for m in out if any(v is not None for v in m["values"].values())] if len(tickers) > 1 else []


def analyze(tickers: set[str], f: Filters) -> dict:
    _load()
    scope = sorted(t for t in tickers if t in _ROWS and (f.ticker is None or t == f.ticker))
    years = sorted({y for t in scope for y in _ROWS[t]})
    year = f.year or (max(years) if years else None)
    companies = [_company(t, year, f) for t in scope if year in _ROWS[t]]
    comparison = _comparison(companies, year) if year and len(companies) > 1 else []
    for c in companies:
        c.pop("_rows")
    return {"filters": {"ticker": f.ticker, "year": year, "assessment": f.assessment},
            "years": years, "year": year, "companies": companies, "comparison": comparison,
            "rule": {"rate_pts": RATE_PTS, "rate_rel_pct": RATE_REL_PCT, "other_pct": OTHER_PCT,
                     "text": f"A change counts when a rate moves by {RATE_PTS:g} point or {RATE_REL_PCT:g}% of its own "
                             f"level, or any other measure by {OTHER_PCT:g}%; smaller moves are shown as flat."}}
