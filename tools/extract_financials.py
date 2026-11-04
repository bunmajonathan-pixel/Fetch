"""
extract_financials.py — turn the quarterly-report PDFs into structured CSVs.

    python tools/extract_financials.py            # write CSVs, run all checks
    python tools/extract_financials.py --check    # run the checks only

Reads   data/individual/<TICKER>/<TICKER>_Qn-YYYY_Quarterly_Report.pdf
Writes  data/individual/financials.csv   one row per company per quarter
        data/individual/segments.csv     one row per company per quarter per segment

The PDFs stay the source of truth.  The analytics read the CSVs (never the PDF
text at request time), and this script refuses to write them unless the
numbers reconcile:

  1. Every statement adds up (revenue - cost = gross profit, ... , net income).
  2. Segments sum to total revenue.
  3. The "prior quarter" and "prior-year quarter" columns of each report equal
     the headline numbers of the report they refer to.
  4. The three-year summary PDF of each company matches the individual reports.

Amounts are in $ millions (EPS in $, shares in millions) with the sign used in
the PDF: costs, tax, capex and shareholder returns are negative.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
IND_DIR  = ROOT / "data" / "individual"
OUT_FIN  = IND_DIR / "financials.csv"
OUT_SEG  = IND_DIR / "segments.csv"

NUM = re.compile(r"^[(+\-]?\$?[\d,]+(\.\d+)?\)?%?$|^—$")

# PDF row label -> CSV column
INCOME = {
    "Revenue": "revenue", "Cost of revenue": "cost_of_revenue",
    "Gross profit": "gross_profit", "Research and development": "rd",
    "Selling, general and admin.": "sga", "Operating income": "operating_income",
    "Net interest income (expense)": "net_interest", "Pre-tax income": "pretax_income",
    "Income tax": "income_tax", "Net income": "net_income",
    "Diluted shares (M)": "diluted_shares", "Diluted EPS ($)": "eps",
}
BALANCE = {
    "Cash and equivalents": "cash", "Accounts receivable": "accounts_receivable",
    "Inventory": "inventory", "Property, plant and equipment": "ppe",
    "Goodwill": "goodwill", "Other assets": "other_assets",
    "Total assets": "total_assets", "Total debt": "total_debt",
    "Other liabilities": "other_liabilities", "Shareholders' equity": "equity",
}
CASHFLOW = {
    "Net income": "cf_net_income", "Depreciation and amortization": "d_and_a",
    "Stock-based compensation": "sbc", "Working capital and other": "working_capital",
    "Operating cash flow": "operating_cash_flow", "Capital expenditures": "capex",
    "Free cash flow": "free_cash_flow", "Dividends and buybacks": "shareholder_returns",
    "Net debt issued (repaid)": "net_debt_issued", "Change in cash": "change_in_cash",
}
COLUMNS = (["ticker", "period", "year", "quarter"]
           + list(INCOME.values()) + list(BALANCE.values())
           + [c for c in CASHFLOW.values() if c != "cf_net_income"])


class ExtractError(Exception):
    pass


def _num(tok: str) -> float | None:
    """'(832.4)' -> -832.4, '1,221.8' -> 1221.8, '—' -> None, '+2.2%' -> 2.2"""
    tok = tok.strip()
    if tok == "—":
        return None
    neg = tok.startswith("(") and tok.endswith(")")
    clean = tok.strip("()").replace(",", "").replace("$", "").replace("%", "").replace("+", "")
    val = float(clean)
    return -val if neg else val


def _table(lines: list[str], start: int, ncols: int) -> tuple[dict[str, list], int]:
    """Read `label, v1..vN` groups from `start` until the pattern stops."""
    rows: dict[str, list] = {}
    i = start
    while i + ncols < len(lines):
        label = lines[i]
        vals = lines[i + 1:i + 1 + ncols]
        if NUM.match(label) or not all(NUM.match(v) for v in vals):
            break
        rows[label] = [_num(v) for v in vals]
        i += 1 + ncols
    return rows, i


def _after(lines: list[str], marker: str, *, skip: int = 0, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if lines[i] == marker:
            return i + 1 + skip
    raise ExtractError(f"marker {marker!r} not found")


def _pdf_lines(path: Path) -> list[str]:
    from pypdf import PdfReader
    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_report(path: Path) -> dict:
    """Parse one quarterly report into {'row': {...}, 'prior_q': {...}, 'prior_y': {...}, 'segments': {...}}."""
    lines = _pdf_lines(path)
    m = re.match(r"([A-Z]+)_Q([1-4])-(\d{4})_Quarterly_Report", path.stem)
    if not m:
        raise ExtractError(f"unexpected file name {path.name}")
    ticker, qtr, year = m.group(1), int(m.group(2)), int(m.group(3))

    # Income statement: label + (this qtr, prior qtr, prior-yr qtr, QoQ, YoY)
    start = _after(lines, "YoY", start=_after(lines, "This qtr"))
    inc, _ = _table(lines, start, 5)
    missing = [k for k in INCOME if k not in inc]
    if missing:
        raise ExtractError(f"{path.name}: income statement rows missing: {missing}")

    # Segments: label + (revenue, % of total, QoQ, YoY)
    s0 = _after(lines, "YoY", start=_after(lines, "% of total"))
    seg_rows, _ = _table(lines, s0, 4)
    total = seg_rows.pop("Total revenue", None)
    if total is None or not seg_rows:
        raise ExtractError(f"{path.name}: segment table not found")

    # Balance sheet and cash flow: label + one value
    b0 = _after(lines, "$M", start=_after(lines, "Balance sheet (period end)"))
    bal, _ = _table(lines, b0, 1)
    c0 = _after(lines, "$M", start=_after(lines, "Cash flow (quarter)"))
    cf, _ = _table(lines, c0, 1)
    for table, want, name in ((bal, BALANCE, "balance sheet"), (cf, CASHFLOW, "cash flow")):
        miss = [k for k in want if k not in table]
        if miss:
            raise ExtractError(f"{path.name}: {name} rows missing: {miss}")

    def col(idx: int) -> dict:
        return {INCOME[k]: inc[k][idx] for k in INCOME}

    row = {"ticker": ticker, "period": f"Q{qtr}-{year}", "year": year, "quarter": qtr}
    row.update(col(0))
    row.update({BALANCE[k]: bal[k][0] for k in BALANCE})
    row.update({CASHFLOW[k]: cf[k][0] for k in CASHFLOW if CASHFLOW[k] != "cf_net_income"})
    cf_ni = cf["Net income"][0]
    if abs(cf_ni - row["net_income"]) > 0.051:
        raise ExtractError(f"{path.name}: cash-flow net income {cf_ni} != income statement {row['net_income']}")

    return {
        "row": row, "prior_q": col(1), "prior_y": col(2),
        "segments": {k: v[0] for k, v in seg_rows.items()},
        "segment_total": total[0],
    }


def parse_summary(path: Path) -> dict[str, dict]:
    """The three-year summary table: {'Q3-2023': {'revenue':..., 'gross_margin':..., ...}}."""
    lines = _pdf_lines(path)
    i = _after(lines, "Cash", start=_after(lines, "Quarterly results at a glance"))
    out: dict[str, dict] = {}
    while i + 7 < len(lines) and re.match(r"^Q[1-4] \d{4}$", lines[i]):
        q, y = lines[i].split()
        v = [_num(x) for x in lines[i + 1:i + 8]]
        out[f"{q}-{y}"] = dict(zip(
            ("revenue", "gross_margin", "operating_income", "net_income", "eps", "free_cash_flow", "cash"), v))
        i += 8
    return out


def _close(a, b, tol) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def reconcile(reports: dict[tuple, dict], summaries: dict[str, dict[str, dict]]) -> list[str]:
    """Return a list of human-readable problems (empty = everything reconciles)."""
    bad: list[str] = []
    for (tk, period), rep in sorted(reports.items()):
        r, tag = rep["row"], f"{tk} {period}"
        # 1. statements add up (figures are rounded to 0.1, so allow a few tenths)
        if not _close(r["revenue"] + r["cost_of_revenue"], r["gross_profit"], 0.25):
            bad.append(f"{tag}: revenue + cost != gross profit")
        if not _close(r["gross_profit"] + r["rd"] + r["sga"], r["operating_income"], 0.35):
            bad.append(f"{tag}: gross profit - opex != operating income")
        if not _close(r["operating_income"] + r["net_interest"], r["pretax_income"], 0.25):
            bad.append(f"{tag}: operating income + interest != pre-tax income")
        if not _close(r["pretax_income"] + r["income_tax"], r["net_income"], 0.25):
            bad.append(f"{tag}: pre-tax + tax != net income")
        if not _close(sum(rep["segments"].values()), rep["segment_total"], 0.35) \
                or not _close(rep["segment_total"], r["revenue"], 0.25):
            bad.append(f"{tag}: segments do not sum to total revenue")
        # 3. comparison columns match the report they refer to
        y, q = r["year"], r["quarter"]
        pq = (f"Q{q - 1}-{y}") if q > 1 else f"Q4-{y - 1}"
        py = f"Q{q}-{y - 1}"
        for key, label, col in ((pq, "prior quarter", "prior_q"), (py, "prior-year quarter", "prior_y")):
            other = reports.get((tk, key))
            if other is None:
                continue
            for k in ("revenue", "gross_profit", "operating_income", "net_income", "eps"):
                if not _close(rep[col][k], other["row"][k], 0.051 if k != "eps" else 0.0051):
                    bad.append(f"{tag}: {label} {k} {rep[col][k]} != {key} report {other['row'][k]}")
    # 4. summary PDFs
    for tk, table in summaries.items():
        for period, s in table.items():
            rep = reports.get((tk, period))
            if rep is None:
                bad.append(f"{tk} {period}: in summary but no quarterly report")
                continue
            r = rep["row"]
            gm = round(100 * r["gross_profit"] / r["revenue"], 1)
            for name, mine, tol in (("revenue", r["revenue"], 0.051), ("gross_margin", gm, 0.11),
                                    ("operating_income", r["operating_income"], 0.051),
                                    ("net_income", r["net_income"], 0.051), ("eps", r["eps"], 0.0051),
                                    ("free_cash_flow", r["free_cash_flow"], 0.051),
                                    ("cash", r["cash"], 0.51)):
                if not _close(s[name], mine, tol):
                    bad.append(f"{tk} {period}: summary {name} {s[name]} != report {mine}")
    return bad


def load_all() -> tuple[dict, dict]:
    reports: dict[tuple, dict] = {}
    summaries: dict[str, dict] = {}
    for path in sorted(IND_DIR.glob("*/*_Quarterly_Report.pdf")):
        rep = parse_report(path)
        reports[(rep["row"]["ticker"], rep["row"]["period"])] = rep
    for path in sorted(IND_DIR.glob("*/*_Summary_*.pdf")):
        summaries[path.name.split("_")[0]] = parse_summary(path)
    return reports, summaries


def _sort_key(item):
    (tk, _), rep = item
    return (tk, rep["row"]["year"], rep["row"]["quarter"])


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    reports, summaries = load_all()
    print(f"parsed {len(reports)} quarterly reports, {len(summaries)} summaries")
    problems = reconcile(reports, summaries)
    if problems:
        print(f"\n{len(problems)} reconciliation problem(s):")
        for p in problems:
            print("  -", p)
        return 1
    print("all statements, segments, comparison columns and summaries reconcile")
    if check_only:
        return 0

    ordered = sorted(reports.items(), key=_sort_key)
    with OUT_FIN.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for _, rep in ordered:
            w.writerow({k: ("" if v is None else v) for k, v in rep["row"].items()})
    with OUT_SEG.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "period", "year", "quarter", "segment", "revenue"])
        for _, rep in ordered:
            r = rep["row"]
            for seg, rev in rep["segments"].items():
                w.writerow([r["ticker"], r["period"], r["year"], r["quarter"], seg, rev])
    print(f"wrote {OUT_FIN} ({len(ordered)} rows) and {OUT_SEG}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
