"""
pdf.py — A Source that indexes a directory of PDF files, one Document per page.

Requires: pypdf  (pip install pypdf)

Each page of each PDF becomes one Document:
  id       = "<stem>__p<page_number>"
  content  = extracted text of that page
  metadata = { ticker, period, report_type, category, path, stem, page, total_pages }

The source recursively walks *root*, so all sub-folders are included automatically.
The folder name each PDF lives in is stored as ``category`` in the metadata.

Filename convention (best-effort parse):
  <TICKER>_<PERIOD>_<rest>.pdf          → ticker=BLFD, period=Q1-2024, report_type=Quarterly Report
  <TICKER>_<CategoryWords>.pdf          → ticker=BLFD, period="",       report_type=Ad-Hoc and Troubleshooting
  <TICKER>_<CompanyName>_<DocType>_<Y>  → ticker=BLFD, period=2024,     report_type=Customer Behavior
"""
from __future__ import annotations
import re
from pathlib import Path

from ..types import Document, Query
from .base import Source

# ---------------------------------------------------------------------------
# Period filter helpers
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(
    r'\b(Q[1-4](?:[- ]\d{4})?|\d{4}(?:[- ]Q[1-4])?)\b',
    re.IGNORECASE,
)

# Common English stopwords that are too generic to use as the sole keyword signal
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "from", "is", "it", "its", "as", "be", "this",
    "that", "are", "was", "were", "but", "not", "have", "has",
}


def _meaningful_terms(terms: list[str]) -> list[str]:
    """Filter out pure stopwords, keeping at least one term if all are stopwords."""
    filtered = [t for t in terms if t not in _STOPWORDS and len(t) > 1]
    return filtered if filtered else terms


def _periods_from_query(text: str) -> set[str]:
    return {m.group().upper().replace(" ", "-") for m in _PERIOD_RE.finditer(text)}


def _period_matches(doc_period: str, query_periods: set[str]) -> bool:
    if not query_periods:
        return True
    dp = doc_period.upper()
    for qp in query_periods:
        if qp == dp:
            return True
        if re.match(r'^Q[1-4]$', qp) and dp.startswith(qp):
            return True
        if re.match(r'^\d{4}$', qp) and qp in dp:
            return True
    return False


# ---------------------------------------------------------------------------
# Filename parser
# ---------------------------------------------------------------------------

def _parse_filename(stem: str, folder_name: str = "") -> dict:
    """
    Extract ticker, period, report_type, and category from a filename stem.

    Handles two main patterns:
      BLFD_Q1-2024_Quarterly_Report      → period=Q1-2024, report_type=Quarterly Report
      BLFD_BrightLeaf_Customer_Behavior_2024 → period=2024, report_type=Customer Behavior
      BLFD_Ad-Hoc_and_Troubleshooting    → period="", report_type=Ad-Hoc and Troubleshooting
    """
    parts = stem.split("_")
    ticker = parts[0].upper() if parts else stem.upper()

    # Category comes from the folder the file lives in
    category = folder_name.replace("_", " ") if folder_name else ""

    if len(parts) < 2:
        return {"ticker": ticker, "period": "", "report_type": "", "category": category}

    # Check if the second token looks like a period (e.g. Q1-2024, Summary)
    second = parts[1]
    period_match = _PERIOD_RE.match(second) if second else None

    if period_match:
        # Standard pattern: TICKER_PERIOD_rest
        period = second
        report_type = " ".join(parts[2:]).replace("-", " ") if len(parts) > 2 else ""
    else:
        # Non-period pattern: look for a year anywhere in the remaining tokens
        year_tokens = [p for p in parts[1:] if re.match(r'^\d{4}$', p)]
        period = year_tokens[0] if year_tokens else ""
        # report_type = all non-ticker, non-year tokens joined
        name_tokens = [p for p in parts[1:] if not re.match(r'^\d{4}$', p)]
        report_type = " ".join(name_tokens).replace("-", " ")
        # Strip the company's own long name prefix if it appears (e.g. "BrightLeaf" in BLFD files)
        # by using the folder category as a cleaner label when it's set
        if category and not report_type:
            report_type = category

    return {
        "ticker":      ticker,
        "period":      period,
        "report_type": report_type,
        "category":    category,
    }


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError("pypdf is required for PDFSource: pip install pypdf") from exc
    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

class PDFSource(Source):
    """
    Indexes every PDF under *root* (recursively), one Document per page.

    Parameters
    ----------
    root:
        Directory to scan recursively for ``*.pdf`` files.
    name:
        Source name shown in Document.source and logs.
    allowed_tickers:
        If provided, only PDFs whose filename starts with one of these tickers
        are loaded. Useful for per-user scoping at the source level.
    """

    def __init__(self, root: str | Path, name: str = "pdf",
                 allowed_tickers: set[str] | None = None):
        self.name = name
        self.root = Path(root)
        self._allowed = {t.upper() for t in allowed_tickers} if allowed_tickers else None
        self._docs: list[Document] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        for path in sorted(self.root.rglob("*.pdf")):
            stem = path.stem
            folder_name = path.parent.name
            meta = _parse_filename(stem, folder_name)
            if self._allowed and meta["ticker"] not in self._allowed:
                continue
            pages = _extract_pages(path)
            total = len(pages)
            for i, text in enumerate(pages, start=1):
                self._docs.append(Document(
                    id=f"{stem}__p{i}",
                    content=text,
                    source=self.name,
                    metadata={
                        **meta,
                        "path":        str(path),
                        "stem":        stem,
                        "page":        i,
                        "total_pages": total,
                    },
                ))
        self._loaded = True

    def fetch(self, query: Query) -> list[Document]:
        self._load()
        all_terms = query.text.lower().split()
        terms = _meaningful_terms(all_terms)
        query_periods = _periods_from_query(query.text)

        # 1. Period filter
        period_filtered = [
            d for d in self._docs
            if _period_matches(d.metadata.get("period", ""), query_periods)
        ]

        # 2. Keyword filter — search content + category + report_type metadata
        def _doc_matches(d: Document) -> bool:
            haystack = (
                d.content.lower()
                + " " + d.metadata.get("category", "").lower()
                + " " + d.metadata.get("report_type", "").lower()
            )
            return any(t in haystack for t in terms)

        matched = [d for d in period_filtered if _doc_matches(d)]

        # 3. Fallback: return all period-filtered if nothing matched
        return matched if matched else period_filtered
