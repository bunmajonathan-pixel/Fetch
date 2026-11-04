"""
generate_supplier_contracts.py — builds the synthetic "Supplier Contracts" PDFs.

One PDF per company per expiry quarter, written to
``data/Supplier_Contracts/`` so the existing
PDFSource pipeline indexes them with no code changes:

    <TICKER>_<Qn-YYYY>_Supplier_Contracts.pdf
      -> ticker, period = expiry quarter, report_type = "Supplier Contracts",
         category = "Supplier Contracts"

The period in the filename is the quarter in which the listed contracts expire,
so a query such as "active supplier contracts expiring in Q4 2026" is narrowed
to the Q4-2026 files by PDFSource's existing period filter, and the per-ticker
ACL then limits each user to their own company's register.

All companies, suppliers and figures are fictional.  Data is hand-written (not
random) so regenerating produces identical files.

Usage (from the project root; needs `pip install reportlab`):
    python tools/generate_supplier_contracts.py
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "data" / "Supplier_Contracts"

AS_OF = date(2026, 11, 4)

COMPANIES = {
    "BLFD": ("BrightLeaf Foods Co.", "Consumer Packaged Goods"),
    "HDYN": ("Halcyon Dynamics Inc.", "Industrial Automation"),
    "MCLD": ("Meridian Cloudworks Corp.", "Enterprise Software"),
}

QUARTER_LABEL = {"Q3-2026": "Q3 2026", "Q4-2026": "Q4 2026", "Q1-2027": "Q1 2027"}

# (contract_id, supplier, category, expiry ISO date, status, annual value $K, owner)
CONTRACTS: dict[str, dict[str, list[tuple]]] = {
    "BLFD": {
        "Q3-2026": [
            ("BLFD-SC-2301", "Golden Plains Grain Co.",   "Ingredients", "2026-07-31", "Renewed",    4200, "Procurement"),
            ("BLFD-SC-2307", "NorthStar Cold Freight",    "Logistics",   "2026-08-15", "Expired",    2850, "Logistics"),
            ("BLFD-SC-2312", "Harbor Packaging Ltd.",     "Packaging",   "2026-09-30", "Renewed",    1900, "Operations"),
            ("BLFD-SC-2318", "Verdant Dairy Collective",  "Ingredients", "2026-09-12", "Terminated", 3100, "Procurement"),
        ],
        "Q4-2026": [
            ("BLFD-SC-2402", "Sunridge Sweeteners Inc.",      "Ingredients", "2026-10-31", "Expired",    1750, "Procurement"),
            ("BLFD-SC-2405", "Pacific Cold Chain LLC",        "Logistics",   "2026-11-15", "Active",     5200, "Logistics"),
            ("BLFD-SC-2409", "Redwood Label & Print",         "Packaging",   "2026-11-30", "Active",      640, "Operations"),
            ("BLFD-SC-2411", "Cedar Mill Flour Co.",          "Ingredients", "2026-12-15", "Active",     3800, "Procurement"),
            ("BLFD-SC-2414", "Summit Pest Control Services",  "Facilities",  "2026-12-31", "Active",      220, "Quality"),
            ("BLFD-SC-2416", "Eastbay Pallet Systems",        "Logistics",   "2026-12-31", "Active",      910, "Logistics"),
            ("BLFD-SC-2419", "FreshGuard Lab Testing",        "Quality",     "2026-12-01", "Terminated",  380, "Quality"),
        ],
        "Q1-2027": [
            ("BLFD-SC-2503", "Orchard Lane Fruit Growers",    "Ingredients", "2027-01-31", "Active",     2600, "Procurement"),
            ("BLFD-SC-2506", "Bluewater Shipping Agents",     "Logistics",   "2027-02-28", "Active",     1450, "Logistics"),
            ("BLFD-SC-2513", "Ironwood Maintenance Group",    "Facilities",  "2027-03-15", "Active",      760, "Operations"),
            ("BLFD-SC-2510", "ClearPack Films",               "Packaging",   "2027-03-31", "Active",     1120, "Operations"),
        ],
    },
    "HDYN": {
        "Q3-2026": [
            ("HDYN-SC-2302", "Precision Castings Group",      "Components",  "2026-07-15", "Renewed",    6400, "Manufacturing"),
            ("HDYN-SC-2308", "Lumen Sensor Works",            "Electronics", "2026-08-31", "Expired",    2300, "Engineering"),
            ("HDYN-SC-2314", "Tri-State Steel Service",       "Materials",   "2026-09-30", "Renewed",    3900, "Procurement"),
        ],
        "Q4-2026": [
            ("HDYN-SC-2403", "Atlas Motion Controls",         "Electronics", "2026-10-15", "Expired",    2750, "Engineering"),
            ("HDYN-SC-2406", "Kestrel Servo Motors",          "Components",  "2026-11-20", "Active",     4850, "Manufacturing"),
            ("HDYN-SC-2408", "Northfield Wire & Cable",       "Materials",   "2026-11-30", "Active",     1340, "Procurement"),
            ("HDYN-SC-2412", "Orbis Robotics Integrators",    "Services",    "2026-12-10", "Active",     3200, "Engineering"),
            ("HDYN-SC-2415", "Ridgeline Industrial Coatings", "Materials",   "2026-12-31", "Active",      870, "Manufacturing"),
            ("HDYN-SC-2417", "Vantage Freight Solutions",     "Logistics",   "2026-12-31", "Active",     1960, "Supply Chain"),
            ("HDYN-SC-2420", "BlueArc Calibration Labs",      "Services",    "2026-12-05", "Terminated",  410, "Quality"),
        ],
        "Q1-2027": [
            ("HDYN-SC-2504", "Helix Bearing Co.",             "Components",  "2027-01-31", "Active",     2150, "Manufacturing"),
            ("HDYN-SC-2507", "Zenith PLC Systems",            "Electronics", "2027-02-28", "Active",     3550, "Engineering"),
            ("HDYN-SC-2511", "Crestview Packaging Supply",    "Logistics",   "2027-03-31", "Active",      590, "Supply Chain"),
        ],
    },
    "MCLD": {
        "Q3-2026": [
            ("MCLD-SC-2303", "Skyline Colocation Partners",   "Data Center", "2026-07-31", "Renewed",   12800, "Infrastructure"),
            ("MCLD-SC-2309", "Quanta Network Transit",        "Networking",  "2026-08-31", "Expired",    4600, "Infrastructure"),
            ("MCLD-SC-2315", "Polaris Analytics Licensing",   "Software",    "2026-09-30", "Terminated", 1850, "Engineering"),
            ("MCLD-SC-2317", "Beacon Security Audits",        "Services",    "2026-09-15", "Renewed",     720, "Security"),
        ],
        "Q4-2026": [
            ("MCLD-SC-2404", "Cobalt Hardware Leasing",       "Hardware",    "2026-10-31", "Expired",    5300, "Infrastructure"),
            ("MCLD-SC-2407", "Stratus Colocation Group",      "Data Center", "2026-11-30", "Active",    14500, "Infrastructure"),
            ("MCLD-SC-2410", "Meridian Edge Carriers",        "Networking",  "2026-11-15", "Active",     6100, "Infrastructure"),
            ("MCLD-SC-2413", "Lattice Database Software",     "Software",    "2026-12-31", "Active",     3900, "Engineering"),
            ("MCLD-SC-2418", "Sentinel SOC Services",         "Services",    "2026-12-15", "Active",     2250, "Security"),
            ("MCLD-SC-2421", "Ember Support Outsourcing",     "Services",    "2026-12-31", "Active",     1480, "Customer Success"),
            ("MCLD-SC-2422", "Vector Observability Inc.",     "Software",    "2026-12-01", "Terminated",  940, "Engineering"),
        ],
        "Q1-2027": [
            ("MCLD-SC-2505", "Nimbus GPU Capacity",           "Hardware",    "2027-01-31", "Active",     9800, "Infrastructure"),
            ("MCLD-SC-2508", "Harbor Payments Gateway",       "Software",    "2027-02-28", "Active",     1700, "Finance"),
            ("MCLD-SC-2512", "Tessera Compliance Advisory",   "Services",    "2027-03-31", "Active",      830, "Security"),
        ],
    },
}

COLUMNS = ["Contract ID", "Supplier", "Category", "Expiry date", "Status", "Annual value ($K)", "Owner"]
COL_WIDTHS = [1.15, 2.2, 1.1, 1.05, 0.9, 1.25, 1.35]  # inches, landscape letter


def _fmt_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%b %d, %Y").replace(" 0", " ")


def _money(k: int) -> str:
    return f"{k:,}"


def build_pdf(ticker: str, period: str, rows: list[tuple]) -> Path:
    name, industry = COMPANIES[ticker]
    label = QUARTER_LABEL[period]
    path = OUT_DIR / f"{ticker}_{period}_Supplier_Contracts.pdf"

    ss = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#555555"))
    title = ParagraphStyle("title", parent=ss["Title"], alignment=0, fontSize=20, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=10, textColor=colors.HexColor("#333333"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=9, leading=12)

    rows = sorted(rows, key=lambda r: r[3])
    active = [r for r in rows if r[4] == "Active"]
    active_value = sum(r[5] for r in active)

    story = [
        Paragraph(f"{name} ({ticker}) | Fictional company and synthetic data for illustrative purposes only", small),
        Spacer(1, 6),
        Paragraph(f"{name} ({ticker})", title),
        Paragraph(
            f"Supplier Contracts | {industry} | Contracts expiring in {label} | "
            f"Status as of {AS_OF.strftime('%B')} {AS_OF.day}, {AS_OF.year}", sub),
        Paragraph(f"Supplier contracts expiring in {label}", h2),
        Paragraph(
            f"{len(rows)} supplier contracts have an expiry date in {label}. "
            f"{len(active)} active supplier contracts expire in {label}, "
            f"with a combined annual value of ${_money(active_value)}K. "
            f"Status values: Active (in force), Expired (end date passed), "
            f"Renewed (replaced by a new contract), Terminated (ended early).", body),
        Spacer(1, 8),
    ]

    data = [COLUMNS] + [
        [cid, sup, cat, _fmt_date(exp), status, _money(val), owner]
        for cid, sup, cat, exp, status, val, owner in rows
    ]
    table = Table(data, colWidths=[w * inch for w in COL_WIDTHS], repeatRows=1)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (5, 0), (5, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0da")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, r in enumerate(rows, start=1):
        if r[4] != "Active":
            style.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#8a3b12")))
    table.setStyle(TableStyle(style))
    story.append(table)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Annual value is the contracted spend per year in thousands of US dollars. "
        "Owner is the internal department accountable for the contract.", small))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path), pagesize=landscape(letter), leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=f"{ticker} Supplier Contracts expiring {label}", author="Fetch sample data generator",
        invariant=True,  # fixed timestamps -> reproducible bytes
    )
    doc.build(story)
    return path


def main() -> None:
    for ticker, by_period in CONTRACTS.items():
        for period, rows in by_period.items():
            print("wrote", build_pdf(ticker, period, rows).relative_to(BASE_DIR))


if __name__ == "__main__":
    main()
