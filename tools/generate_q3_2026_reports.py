"""
generate_q3_2026_reports.py — write the Q3 2026 quarterly reports for the three demo companies.

    python tools/generate_q3_2026_reports.py        # needs `pip install reportlab`
    python tools/extract_financials.py              # then rebuild the CSVs (it checks the new reports)

The first twelve quarters (Q3 2023 to Q2 2026) were supplied as PDFs.  This script adds the
quarter that has just closed so the demo data runs to the present and has some bad news in it:

  BLFD  flat sales, input costs squeeze gross margin, earnings and cash flow fall (a miss against outlook)
  HDYN  sales and profit grow but cash flow keeps slipping
  MCLD  a strong quarter that beats its outlook

Each report is built from the company's own Q2 2026 and Q3 2025 reports (read from the CSVs the
extractor wrote from those PDFs), so the prior-quarter and prior-year columns match the reports
they refer to.  The report layout is the layout the extractor reads, and the extractor's
reconciliation checks run on the result.  All figures are synthetic.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
IND = ROOT / "data" / "individual"

PERIOD, PREV, YEAR_AGO = "Q3-2026", "Q2-2026", "Q3-2025"
PAGE_NO = 14

NAMES = {"BLFD": "BrightLeaf Foods Co.", "HDYN": "Halcyon Dynamics Inc.", "MCLD": "Meridian Cloudworks Corp."}
ACCENT = {"BLFD": "#2E7D32", "HDYN": "#1F4E79", "MCLD": "#6A1B9A"}
RELEASED = {"BLFD": "November 2, 2026", "HDYN": "October 28, 2026", "MCLD": "October 30, 2026"}

# The story of the quarter, per company.
#   rev_yoy      revenue growth against Q3 2025 (%)
#   gm           gross margin (%)
#   rd, sga      operating expense lines ($M, positive)
#   interest     net interest income (expense), $M
#   tax          effective tax rate (%)
#   shares       diluted shares (M)
#   ocf          operating cash flow ($M)
#   capex        capital expenditure ($M, positive)
#   returns      dividends and buybacks ($M, positive; 0 = none)
#   seg_yoy      year-on-year growth for each segment except the last-named "plug" segment (%)
#   plug         segment that takes whatever revenue is left, so segments sum to the total
#   note         the sentence about the quarter that goes in the highlights
PLAN = {
    "BLFD": dict(rev_yoy=0.6, gm=29.9, rd=8.4, sga=240.5, interest=-20.4, tax=22.4, shares=298.4,
                 ocf=130.0, capex=43.0, returns=75.9, plug="Snacks",
                 seg_yoy={"Beverages": -1.5, "Frozen and Prepared": -3.0, "International": 7.0},
                 note="Commodity inputs, including oils and sweeteners, ran well above hedged levels and cost roughly two "
                      "points of gross margin; management said several ingredient contracts are being renegotiated. "),
    "HDYN": dict(rev_yoy=6.7, gm=39.3, rd=47.4, sga=83.4, interest=-4.7, tax=22.5, shares=140.6,
                 ocf=58.0, capex=19.8, returns=45.2, plug="Factory Automation",
                 seg_yoy={"Robotics and Vision": 12.0, "Services and Software": 13.5},
                 note="Profit grew, but working capital absorbed cash again as customers took longer to pay and inventory "
                      "was built ahead of the holiday backlog, so free cash flow fell. "),
    "MCLD": dict(rev_yoy=11.0, gm=74.4, rd=99.0, sga=152.5, interest=15.5, tax=12.5, shares=118.1,
                 ocf=128.0, capex=11.0, returns=0.0, plug="Subscription Platform",
                 seg_yoy={"Analytics Add-ons": 19.0, "Professional Services": 4.0},
                 note="Large-account expansion and the analytics add-ons drove revenue above the top of the outlook range. "),
}

INCOME_ROWS = [  # label, key, bold
    ("Revenue", "revenue", False), ("Cost of revenue", "cost_of_revenue", False),
    ("Gross profit", "gross_profit", True), ("Research and development", "rd", False),
    ("Selling, general and admin.", "sga", False), ("Operating income", "operating_income", True),
    ("Net interest income (expense)", "net_interest", False), ("Pre-tax income", "pretax_income", False),
    ("Income tax", "income_tax", False), ("Net income", "net_income", True),
    ("Diluted shares (M)", "diluted_shares", False), ("Diluted EPS ($)", "eps", True),
]
INCOME_KEYS = [k for _, k, _ in INCOME_ROWS]


# ---------------------------------------------------------------------------
# Inputs: the two reports this one is compared with
# ---------------------------------------------------------------------------

def _f(v: str):
    return float(v) if v not in ("", None) else None


def load_inputs() -> tuple[dict, dict, dict]:
    fin: dict[tuple, dict] = {}
    with (IND / "financials.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            fin[(r["ticker"], r["period"])] = {k: (_f(v) if k not in ("ticker", "period") else v) for k, v in r.items()}
    seg: dict[tuple, dict] = {}
    with (IND / "segments.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            seg.setdefault((r["ticker"], r["period"]), {})[r["segment"]] = float(r["revenue"])
    return fin, seg, {}


# ---------------------------------------------------------------------------
# The quarter's numbers
# ---------------------------------------------------------------------------

def r1(x: float) -> float:
    return round(x + 1e-9, 1)


def build(tk: str, fin: dict, seg: dict) -> dict:
    p, prev, ya = PLAN[tk], fin[(tk, PREV)], fin[(tk, YEAR_AGO)]
    rev = r1(ya["revenue"] * (1 + p["rev_yoy"] / 100))
    cost = -r1(rev * (1 - p["gm"] / 100))
    gp = r1(rev + cost)
    rd, sga = -p["rd"], -p["sga"]
    op = r1(gp + rd + sga)
    pre = r1(op + p["interest"])
    tax = -r1(pre * p["tax"] / 100)
    ni = r1(pre + tax)
    eps = round(ni / p["shares"] + 1e-9, 2)
    row = dict(revenue=rev, cost_of_revenue=cost, gross_profit=gp, rd=rd, sga=sga, operating_income=op,
               net_interest=p["interest"], pretax_income=pre, income_tax=tax, net_income=ni,
               diluted_shares=p["shares"], eps=eps)

    da = r1(prev["d_and_a"] * 1.002)
    sbc = r1(prev["sbc"] * 1.01)
    wc = r1(p["ocf"] - ni - da - sbc)
    ocf = r1(ni + da + sbc + wc)
    capex = -p["capex"]
    fcf = r1(ocf + capex)
    returns = -p["returns"] if p["returns"] else None
    change = r1(ocf + capex + (returns or 0.0))
    cash = r1(prev["cash"] + change)
    scale = rev / prev["revenue"]
    ar = r1(prev["accounts_receivable"] * scale * (1.03 if tk == "HDYN" else 1.0))
    inv = r1(prev["inventory"] * scale * (1.06 if tk == "HDYN" else 1.0)) if prev["inventory"] is not None else None
    ppe = r1(prev["ppe"] - capex - da)
    goodwill, other = prev["goodwill"], prev["other_assets"]
    assets = r1(cash + ar + (inv or 0.0) + ppe + goodwill + other)
    debt = prev["total_debt"]
    equity = r1(prev["equity"] + ni + sbc + (returns or 0.0))
    liab = r1(assets - debt - equity)
    row.update(cash=cash, accounts_receivable=ar, inventory=inv, ppe=ppe, goodwill=goodwill, other_assets=other,
               total_assets=assets, total_debt=debt, other_liabilities=liab, equity=equity,
               d_and_a=da, sbc=sbc, working_capital=wc, operating_cash_flow=ocf, capex=capex,
               free_cash_flow=fcf, shareholder_returns=returns, net_debt_issued=None, change_in_cash=change)

    # segments: named ones grow by their target; the plug segment takes the rest
    s_prev, s_ya = seg[(tk, PREV)], seg[(tk, YEAR_AGO)]
    segs = {name: r1(s_ya[name] * (1 + g / 100)) for name, g in p["seg_yoy"].items()}
    segs[p["plug"]] = r1(rev - sum(segs.values()))
    order = list(s_prev)                      # keep the order the earlier reports use
    segments = {name: segs[name] for name in order}
    return {"row": row, "prev": prev, "ya": ya, "segments": segments, "s_prev": s_prev, "s_ya": s_ya}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def money(v, dec=1):
    if v is None:
        return "—"
    s = f"{abs(v):,.{dec}f}"
    return f"({s})" if v < 0 else s


def pct(new, old):
    if new is None or old in (None, 0):
        return "—"
    return f"{100 * (new - old) / abs(old):+.1f}%"


def ordinal_word(n: float) -> str:
    return f"{n:.1f}%"


def highlights(tk: str, d: dict) -> list[str]:
    r, prev, ya = d["row"], d["prev"], d["ya"]
    yoy = 100 * (r["revenue"] / ya["revenue"] - 1)
    gm, gm_prev = 100 * r["gross_profit"] / r["revenue"], 100 * prev["gross_profit"] / prev["revenue"]
    bps = round((gm - gm_prev) * 100)
    move = "an expansion" if bps >= 0 else "a contraction"
    word = "up" if yoy >= 0 else "down"
    seg_growth = {k: 100 * (v / d["s_ya"][k] - 1) for k, v in d["segments"].items()}
    best = max(seg_growth, key=seg_growth.get)
    p1 = (f"{NAMES[tk]} reported Q3 2026 revenue of ${r['revenue']:,.1f} million, {word} {abs(yoy):.1f}% year over year. "
          f"Gross margin was {gm:.1f}%, {move} of {abs(bps)} basis points from the prior quarter. Operating income was "
          f"${r['operating_income']:,.1f} million ({100 * r['operating_income'] / r['revenue']:.1f}% of revenue), net income was "
          f"${r['net_income']:,.1f} million and diluted EPS was ${r['eps']:.2f}. Free cash flow totaled ${r['free_cash_flow']:,.1f} million, "
          f"and the company ended the quarter with ${r['cash']:,.0f} million in cash.")
    ret = (f"The company returned ${-r['shareholder_returns']:,.1f} million to shareholders through dividends and share repurchases."
           if r["shareholder_returns"] else "The company did not repurchase shares or pay dividends during the quarter.")
    p2 = (f"{best} was the fastest-growing segment, rising {seg_growth[best]:.1f}% year over year. {PLAN[tk]['note']}{ret}")
    return [p1, p2]


def outlook(tk: str, d: dict, fin: dict) -> str:
    r, ya = d["row"], d["ya"]
    q4 = fin[(tk, "Q4-2025")]
    yoy = r["revenue"] / ya["revenue"]
    mid = q4["revenue"] * yoy
    lo, hi = round(mid * 0.97), round(mid * 1.03)
    e_mid = q4["eps"] * (r["net_income"] / ya["net_income"])
    return (f"For Q4 2026, the company expects revenue of ${lo:,} million to ${hi:,} million and diluted EPS of "
            f"${e_mid - 0.02:.2f} to ${e_mid + 0.02:.2f}. This outlook is fictional and reflects current business "
            f"conditions as modeled for this report.")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _table(data, widths, accent, bold_rows=(), align_first_left=True):
    t = Table(data, colWidths=widths, rowHeights=15.5)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), accent), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.6), ("FONT", (0, 1), (-1, -1), "Helvetica", 7.6),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F3F3")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in bold_rows:
        style += [("FONT", (0, i), (-1, i), "Helvetica-Bold", 7.6), ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#EDEDED"))]
    t.setStyle(TableStyle(style))
    return t


def write_pdf(tk: str, d: dict, fin: dict, path: Path) -> None:
    acc = colors.HexColor(ACCENT[tk])
    name = NAMES[tk]
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=19, textColor=acc, leading=22)
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=8.2, textColor=colors.HexColor("#555555"), leading=11, spaceAfter=6)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.5, textColor=acc, leading=13, spaceBefore=6, spaceAfter=3)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=7.9, leading=10.3, spaceAfter=4)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(acc); canvas.setLineWidth(1.2); canvas.line(40, 770, 572, 770)
        canvas.setFont("Helvetica", 6.6); canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(40, 28, f"{name} ({tk}) | Fictional company and synthetic data for illustrative purposes only")
        canvas.drawRightString(572, 28, f"Page {PAGE_NO}")
        canvas.restoreState()

    r, prev, ya = d["row"], d["prev"], d["ya"]
    story = [Paragraph(f"{name}: Q3 2026 Quarterly Report", h1),
             Paragraph(f"Three months ended September 30, 2026 | Results released {RELEASED[tk]} | $ in millions except per share data", sub),
             Paragraph("Highlights", h2)]
    story += [Paragraph(p, body) for p in highlights(tk, d)]

    story.append(Paragraph("Income statement", h2))
    rows = [["Income statement", "This qtr", "Prior qtr", "Prior-yr qtr", "QoQ", "YoY"]]
    for label, key, _ in INCOME_ROWS:
        dec = 2 if key == "eps" else 1
        skip = key == "net_interest"
        rows.append([label, money(r[key], dec), money(prev[key], dec), money(ya[key], dec),
                     "—" if skip else pct(r[key], prev[key]), "—" if skip else pct(r[key], ya[key])])
    bold = [i + 1 for i, (_, _, b) in enumerate(INCOME_ROWS) if b]
    story.append(_table(rows, [170, 72, 72, 72, 73, 73], acc, bold))

    story.append(Paragraph("Segments", h2))
    seg = [["Revenue by segment", "Revenue", "% of total", "QoQ", "YoY"]]
    for k, v in d["segments"].items():
        seg.append([k, money(v), f"{100 * v / r['revenue']:.1f}%", pct(v, d["s_prev"][k]), pct(v, d["s_ya"][k])])
    seg.append(["Total revenue", money(r["revenue"]), "100.0%", pct(r["revenue"], prev["revenue"]), pct(r["revenue"], ya["revenue"])])
    story.append(_table(seg, [190, 85, 85, 86, 86], acc, [len(seg) - 1]))

    story.append(Paragraph("Balance sheet and cash flow", h2))
    bal = [["Balance sheet (period end)", "$M"],
           ["Cash and equivalents", money(r["cash"])], ["Accounts receivable", money(r["accounts_receivable"])],
           ["Inventory", money(r["inventory"])], ["Property, plant and equipment", money(r["ppe"])],
           ["Goodwill", money(r["goodwill"])], ["Other assets", money(r["other_assets"])],
           ["Total assets", money(r["total_assets"])], ["Total debt", money(r["total_debt"])],
           ["Other liabilities", money(r["other_liabilities"])], ["Shareholders' equity", money(r["equity"])]]
    cf = [["Cash flow (quarter)", "$M"],
          ["Net income", money(r["net_income"])], ["Depreciation and amortization", money(r["d_and_a"])],
          ["Stock-based compensation", money(r["sbc"])], ["Working capital and other", money(r["working_capital"])],
          ["Operating cash flow", money(r["operating_cash_flow"])], ["Capital expenditures", money(r["capex"])],
          ["Free cash flow", money(r["free_cash_flow"])], ["Dividends and buybacks", money(r["shareholder_returns"])],
          ["Net debt issued (repaid)", money(r["net_debt_issued"])], ["Change in cash", money(r["change_in_cash"])]]
    both = Table([[_table(bal, [150, 100], acc, [7, 10]), _table(cf, [150, 100], acc, [5, 7, 10])]],
                 colWidths=[266, 266], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])
    story.append(both)
    story += [Paragraph("Outlook", h2), Paragraph(outlook(tk, d, fin), body), Spacer(1, 4)]

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=40, rightMargin=40, topMargin=32, bottomMargin=44,
                            title=f"{name} Q3 2026 Quarterly Report", author=f"{name} (fictional)")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> int:
    fin, seg, _ = load_inputs()
    for tk in PLAN:
        d = build(tk, fin, seg)
        out = IND / tk / f"{tk}_{PERIOD}_Quarterly_Report.pdf"
        write_pdf(tk, d, fin, out)
        r = d["row"]
        print(f"{out.relative_to(ROOT)}  revenue {r['revenue']}  op.income {r['operating_income']}  net income {r['net_income']}  "
              f"EPS {r['eps']:.2f}  FCF {r['free_cash_flow']}  cash {r['cash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
