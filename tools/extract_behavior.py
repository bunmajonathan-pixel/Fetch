"""
extract_behavior.py — turn the customer-behavior report PDFs into one tidy CSV.

    python tools/extract_behavior.py            # write the CSV, run all checks
    python tools/extract_behavior.py --check    # run the checks only

Reads   data/behavior/<TICKER>_*_Customer_Behavior_<YEAR>.pdf
Writes  data/behavior/behavior.csv    one row per company per year per metric

The PDFs stay the source of truth.  The analytics read the CSV, never the PDF
text at request time, and this script refuses to write it unless the numbers
agree with each other and with the quarterly financials:

  1. Both years of a company report exactly the same metrics.
  2. Every "vs. prior year" change printed in a later report equals the change
     between the two years' values (allowing for rounding).
  3. Each customer-mix table (channel, age group, ...) adds up to 100%.
  4. Revenue in the report equals the sum of that year's quarterly reports.
  5. Internal arithmetic holds where a company reports the parts: customers
     rolled forward from new and lost customers, logo churn, seat activation
     and stickiness, revenue per customer.

Columns: ticker, year, section, kind (metric | mix), key, label, value, unit,
printed_change, change_kind, direction, role.  ``unit`` is "%", "$", "$K", "$M" or blank (a
count or a plain number whose scale is in the label).  ``printed_change`` is the
change vs. the prior year exactly as printed (blank in the first year);
``change_kind`` is "pct" (relative change) or "pts" (percentage points).
``direction`` says which way is better: "up", "down" or "neutral" (mix tables,
and measures where bigger is not better or worse).  ``role`` marks the metrics
the analytics build on: "volume" (the size of the customer base), "gain" (new
customers added in the year) and "loss" (customers lost in the year).
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
BEH_DIR = ROOT / "data" / "behavior"
FIN_CSV = ROOT / "data" / "individual" / "financials.csv"
OUT     = BEH_DIR / "behavior.csv"

COLUMNS = ["ticker", "year", "section", "kind", "key", "label", "value", "unit",
           "printed_change", "change_kind", "direction", "role"]

_VALUE  = r"-?\$?[\d,]+(?:\.\d+)?[%KM]?"
_CHANGE = r"[+\-−]\d+(?:\.\d+)?(?:%| pts)"
LINE_2  = re.compile(rf"^(?P<label>.+?) (?P<value>{_VALUE}) (?P<change>{_CHANGE})$")
LINE_1  = re.compile(rf"^(?P<label>.+?) (?P<value>{_VALUE})$")
HEADER  = re.compile(r"^(?P<name>.+?) (?P<year>\d{4})(?: vs\. (?P<prior>\d{4}))?$")
REVENUE = re.compile(r"Revenue was \$([\d,]+(?:\.\d+)?) million(?: \(([+\-−]\d+(?:\.\d+)?)% versus (\d{4})\))?")
FOOTER  = "Fictional company and synthetic data"

# Which way is "better".  Anything not listed is "up" (higher is better);
# customer-mix tables are always neutral.
LOWER_IS_BETTER = {
    "volume_sold_on_promotion", "households_switching_to_private_label", "complaints_per_million_units",
    "customers_lost", "churned_logos", "logo_churn_rate", "average_sales_cycle_days",
    "support_tickets_per_100_customers", "average_first_response_time_hours",
    "time_to_first_value_days", "support_tickets_per_100_customers_per_quarter",
    "top_10_customers_share_of_revenue",
}
NEUTRAL = {"average_order_value_k", "average_contract_value_k", "spend_per_purchase_occasion"}
# metric key -> role, per company.  "volume" is the count that measures the size of the customer base.
ROLES = {
    "BLFD": {"buying_households_millions": "volume"},
    "HDYN": {"active_customer_accounts_year_end": "volume", "new_customers_won": "gain", "customers_lost": "loss"},
    "MCLD": {"customers_year_end": "volume", "new_logos": "gain", "churned_logos": "loss"},
}


def _role(ticker: str, roles: dict) -> str | None:
    return next((k for k, v in ROLES.get(ticker, {}).items() if v == roles), None)


def slug(label: str) -> str:
    s = re.sub(r"\(([^)]*)\)", r" \1 ", label.lower().replace("$", "").replace("+", ""))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def direction(key: str, kind: str) -> str:
    if kind == "mix" or key in NEUTRAL:
        return "neutral"
    return "down" if key in LOWER_IS_BETTER else "up"


def _num(token: str) -> tuple[float, str]:
    t = token.replace(",", "")
    unit = ""
    if t.endswith("%"):
        unit, t = "%", t[:-1]
    elif t.startswith("$"):
        t = t[1:]
        unit = "$"
        if t.endswith("K"):
            unit, t = "$K", t[:-1]
        elif t.endswith("M"):
            unit, t = "$M", t[:-1]
    return float(t), unit


def _change(token: str) -> tuple[float, str]:
    t = token.replace("−", "-")
    if t.endswith("pts"):
        return float(t[:-3]), "pts"
    return float(t[:-1]), "pct"


def parse_report(path: Path) -> dict:
    """Return {"ticker", "year", "revenue", "revenue_change", "rows": [...]}."""
    import pdfplumber
    m = re.match(r"([A-Z]{4})_.*_Customer_Behavior_(\d{4})\.pdf$", path.name)
    if not m:
        raise ValueError(f"unexpected file name {path.name}")
    ticker, year = m.group(1), int(m.group(2))
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and FOOTER not in ln]

    rev = REVENUE.search(" ".join(lines))
    if not rev:
        raise ValueError(f"{path.name}: no revenue sentence")
    out = {"ticker": ticker, "year": year, "revenue": float(rev.group(1).replace(",", "")),
           "revenue_change": float(rev.group(2).replace("−", "-")) if rev.group(2) else None,
           "rows": []}

    try:
        i = lines.index("Behavior metrics") + 1
    except ValueError:
        raise ValueError(f"{path.name}: no 'Behavior metrics' heading") from None
    section, kind = None, "metric"
    for ln in lines[i:]:
        if ln.startswith("Key behavior rates"):
            break
        if ln == "Customer mix":
            kind = "mix"
            continue
        h = HEADER.match(ln)
        if h and int(h.group("year")) == year:
            section = h.group("name")
            continue
        mm = LINE_2.match(ln) or LINE_1.match(ln)
        if not mm or section is None:
            raise ValueError(f"{path.name}: cannot read line {ln!r}")
        label = mm.group("label")
        val, unit = _num(mm.group("value"))
        chg = _change(mm.group("change")) if "change" in mm.groupdict() else (None, "")
        out["rows"].append({
            "ticker": ticker, "year": year, "section": section, "kind": kind, "key": slug(label),
            "label": label, "value": val, "unit": unit,
            "printed_change": "" if chg[0] is None else chg[0], "change_kind": chg[1]})
    return out


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _digits(v: float) -> int:
    s = f"{v:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _half_unit(v: float) -> float:
    return 0.5 * 10 ** -_digits(v)


def _change_ok(kind: str, unit: str, new: float, old: float, printed: float) -> bool:
    if kind == "pts":
        return abs((new - old) - printed) <= _half_unit(new) + _half_unit(old) + 0.051
    if old == 0:
        return False
    exact = 100 * (new / old - 1)
    # worst-case error from rounding both inputs, plus the rounding of the printed value
    slack = 100 * (_half_unit(new) / abs(new) + _half_unit(old) / abs(old)) * (new / old) + 0.051
    return abs(exact - printed) <= slack


def annual_revenue(ticker: str, year: int) -> float | None:
    if not FIN_CSV.exists():
        return None
    with FIN_CSV.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["ticker"] == ticker and int(r["year"]) == year]
    return round(sum(float(r["revenue"]) for r in rows), 1) if len(rows) == 4 else None


def reconcile(reports: dict[tuple, dict]) -> list[str]:
    bad: list[str] = []
    tickers = sorted({t for t, _ in reports})
    for tk in tickers:
        years = sorted(y for t, y in reports if t == tk)
        for y in years:
            rep = reports[(tk, y)]
            fin = annual_revenue(tk, y)
            if fin is None:
                bad.append(f"{tk} {y}: quarterly financials do not cover the full year")
            elif abs(fin - rep["revenue"]) > 0.11:
                bad.append(f"{tk} {y}: revenue {rep['revenue']} != sum of quarters {fin}")
            seen: set = set()
            for r in rep["rows"]:
                if (r["section"], r["key"]) in seen:
                    bad.append(f"{tk} {y}: duplicate metric {r['section']} / {r['label']}")
                seen.add((r["section"], r["key"]))
            # mix tables add up
            mixes: dict[str, float] = {}
            for r in rep["rows"]:
                if r["kind"] == "mix":
                    mixes[r["section"]] = mixes.get(r["section"], 0) + r["value"]
            for name, total in mixes.items():
                if abs(total - 100) > 0.51:
                    bad.append(f"{tk} {y}: '{name}' adds up to {total:.1f}%, not 100%")
        # year over year
        for a, b in zip(years, years[1:]):
            old = {(r["section"], r["key"]): r for r in reports[(tk, a)]["rows"]}
            new = {(r["section"], r["key"]): r for r in reports[(tk, b)]["rows"]}
            if set(old) != set(new):
                bad.append(f"{tk}: metrics differ between {a} and {b}: {sorted(set(old) ^ set(new))}")
                continue
            for k, r in new.items():
                if r["printed_change"] == "":
                    bad.append(f"{tk} {b}: '{r['label']}' has no printed change")
                    continue
                if not _change_ok(r["change_kind"], r["unit"], r["value"], old[k]["value"], r["printed_change"]):
                    bad.append(f"{tk} {b}: '{r['label']}' {old[k]['value']} -> {r['value']} but the report "
                               f"prints {r['printed_change']:+} {r['change_kind']}")
            rc = reports[(tk, b)]["revenue_change"]
            if rc is not None:
                exact = 100 * (reports[(tk, b)]["revenue"] / reports[(tk, a)]["revenue"] - 1)
                if abs(exact - rc) > 0.06:
                    bad.append(f"{tk} {b}: revenue growth {exact:.2f}% but the report says {rc}%")
        bad += _arithmetic(tk, reports, years)
    return bad


def _v(rep: dict, key: str) -> float | None:
    for r in rep["rows"]:
        if r["key"] == key:
            return r["value"]
    return None


def _close(a, b, tol) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _arithmetic(tk: str, reports: dict, years: list[int]) -> list[str]:
    bad: list[str] = []
    for y in years:
        rep = reports[(tk, y)]
        v = lambda k: _v(rep, k)          # noqa: E731
        if tk in ("HDYN", "MCLD"):
            end_key, new_key, lost_key = (_role(tk, r) for r in ("volume", "gain", "loss"))
            end, new, lost, churn = v(end_key), v(new_key), v(lost_key), v("logo_churn_rate")
            start = end - new + lost
            if y - 1 in years:
                prev_end = _v(reports[(tk, y - 1)], end_key)
                if prev_end != start:
                    bad.append(f"{tk} {y}: {prev_end:g} + {new:g} new - {lost:g} lost = {prev_end + new - lost:g}, "
                               f"but year-end customers are {end:g}")
            if not _close(100 * lost / start, churn, 0.06):
                bad.append(f"{tk} {y}: churn {100 * lost / start:.2f}% from {lost:g} lost of {start:g}, "
                           f"report says {churn}%")
        if tk == "MCLD":
            if not _close(100 * v("monthly_active_users_thousands") / v("licensed_seats_thousands"),
                          v("seat_activation_rate_mau_seats"), 0.06):
                bad.append(f"MCLD {y}: seat activation does not equal MAU / seats")
            if not _close(100 * v("daily_active_users_thousands") / v("monthly_active_users_thousands"),
                          v("stickiness_dau_mau"), 0.06):
                bad.append(f"MCLD {y}: stickiness does not equal DAU / MAU")
        if tk == "HDYN":
            rpc = v("revenue_per_active_customer_k")
            if not _close(1000 * rep["revenue"] / v("active_customer_accounts_year_end"), rpc, 0.6):
                bad.append(f"HDYN {y}: revenue per customer {rpc} != revenue / customers")
    return bad


def load_all() -> dict[tuple, dict]:
    reports: dict[tuple, dict] = {}
    for path in sorted(BEH_DIR.glob("*_Customer_Behavior_*.pdf")):
        rep = parse_report(path)
        reports[(rep["ticker"], rep["year"])] = rep
    return reports


def to_rows(reports: dict[tuple, dict]) -> list[dict]:
    out: list[dict] = []
    for (tk, y), rep in sorted(reports.items()):
        out.append({"ticker": tk, "year": y, "section": "Overview", "kind": "metric", "key": "revenue",
                    "label": "Revenue ($M)", "value": rep["revenue"], "unit": "$M",
                    "printed_change": "" if rep["revenue_change"] is None else rep["revenue_change"],
                    "change_kind": "" if rep["revenue_change"] is None else "pct",
                    "direction": "up", "role": "revenue"})
        for r in rep["rows"]:
            out.append({**r, "direction": direction(r["key"], r["kind"]),
                        "role": ROLES.get(tk, {}).get(r["key"], "")})
    return out


def main(argv: list[str]) -> int:
    reports = load_all()
    n = sum(len(r["rows"]) for r in reports.values())
    print(f"parsed {len(reports)} behavior reports, {n} metrics")
    problems = reconcile(reports)
    if problems:
        print(f"\n{len(problems)} reconciliation problem(s):")
        for p in problems:
            print("  -", p)
        return 1
    print("year-over-year changes, mix tables, revenue and internal arithmetic all reconcile")
    if "--check" in argv:
        return 0
    rows = to_rows(reports)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
