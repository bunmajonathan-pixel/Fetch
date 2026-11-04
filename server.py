"""
server.py — Flask HTTP server bridging the browser UI (ui/index.html) to the PDF retrieval backend.

Endpoints
---------
GET  /               → serves ui/index.html
POST /api/signin     { company, id, password }  → { name, id, company, token }
POST /api/retrieve   { query, token }           → { title, subtitle, columns, rows, note? }
POST /api/refresh    { token }                  → { token }   (new token, old one invalidated)
POST /api/signout    { token }                  → { ok: true }
POST /api/analyze/contracts { filters…, token }  → expiry-exposure statistics (scoped per user)
GET  /api/pdf/<ticker>/<path>  (session cookie + ticker ACL) → PDF file
GET  /api/audit      (admin token required)     → { entries: [...] }

Run from the project root:
    python server.py
Then open http://localhost:5000
"""
from __future__ import annotations
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort

from retrieval import (
    Retriever, TTLCache,
    AuthError, login, logout, validate_token,
)
from retrieval.auth import issue_token
from retrieval.sources import PDFSource
from retrieval.types import Query, Document
from retrieval.audit import configure as _audit_configure, record as _audit, recent as _audit_recent
from retrieval import behavior, contracts, db_users, financials

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).parent
# Root that contains ALL category folders (individual, behavior, Supplier_Contracts, …)
DATA_ROOT   = BASE_DIR / "data"
UI_PATH     = BASE_DIR / "ui" / "index.html"
# Runtime files (users.db, audit.log). Created on first run, git-ignored.
# Override with FETCH_INSTANCE_DIR (the test suite points it at a temp folder).
INSTANCE_DIR = Path(os.environ.get("FETCH_INSTANCE_DIR") or BASE_DIR / "instance")

# ---------------------------------------------------------------------------
# Per-company config
# ---------------------------------------------------------------------------
COMPANIES = {
    "BLFD": {"name": "BrightLeaf Foods Co."},
    "HDYN": {"name": "Halcyon Dynamics Inc."},
    "MCLD": {"name": "Meridian Cloudworks Corp."},
}

# One PDFSource per ticker, rooted at DATA_ROOT — the recursive rglob picks up
# every sub-folder automatically; allowed_tickers filters by filename prefix.
_sources: dict[str, PDFSource] = {
    ticker: PDFSource(DATA_ROOT, name=ticker.lower(), allowed_tickers={ticker})
    for ticker in COMPANIES
    if DATA_ROOT.exists()
}

# ---------------------------------------------------------------------------
# Result table schema
# ---------------------------------------------------------------------------
COLUMNS = [
    {"key": "report",    "label": "Report",        "w": "24%"},
    {"key": "ticker",    "label": "Ticker",        "w": "6%"},
    {"key": "category",  "label": "Category",      "w": "14%"},
    {"key": "period",    "label": "Period",        "w": "7%"},
    {"key": "pages",     "label": "Pages",         "w": "5%"},
    {"key": "snippet",   "label": "Matched text",  "w": "22%"},
    {"key": "status",    "label": "Status",        "w": "8%", "type": "status"},
    {"key": "file",      "label": "View",          "w": "7%", "type": "pdf_link"},
]

# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
_audit_configure(INSTANCE_DIR / "audit.log")

# SQLite user store: must run once at startup, before any login
db_users.init_db(INSTANCE_DIR / "users.db")

# Supplier-contract analytics store, rebuilt from the CSV whenever the CSV changes
CONTRACTS_CSV = DATA_ROOT / "Supplier_Contracts" / "contracts.csv"
contracts.init_contracts(INSTANCE_DIR / "contracts.db", CONTRACTS_CSV)

# Quarterly financial trends, read from the CSVs that tools/extract_financials.py builds from the PDFs
financials.init_financials(DATA_ROOT / "individual" / "financials.csv",
                           DATA_ROOT / "individual" / "segments.csv")

# Year-over-year customer-behavior metrics, read from the CSV that tools/extract_behavior.py builds from the PDFs
behavior.init_behavior(DATA_ROOT / "behavior" / "behavior.csv")

# The contract-register PDFs list EVERY department's contracts. A user limited to
# certain departments (user.owners is not None) must not see them, or the record-level
# limit could be bypassed by opening the PDF; they use /api/analyze/contracts instead.
REGISTER_FOLDER   = "Supplier_Contracts"      # folder under data/
REGISTER_CATEGORY = "Supplier Contracts"      # the same folder as shown in search results

# Admin token — set env var AUDIT_TOKEN to enable the /api/audit endpoint.
# Defaults to a disabled sentinel so the endpoint is off unless configured.
AUDIT_TOKEN = os.environ.get("AUDIT_TOKEN", "")

# ---------------------------------------------------------------------------
# Rate limiter  (per session token: max 30 retrieve calls per 60 s)
# ---------------------------------------------------------------------------
_RATE_WINDOW  = 60      # seconds
_RATE_LIMIT   = 30      # requests per window

# token → list of request timestamps
_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate(token: str) -> bool:
    """Return True if the token is within rate limit, False if throttled."""
    now = time.monotonic()
    window_start = now - _RATE_WINDOW
    timestamps = _rate_store[token]
    # Prune old entries
    _rate_store[token] = [t for t in timestamps if t > window_start]
    if len(_rate_store[token]) >= _RATE_LIMIT:
        return False
    _rate_store[token].append(now)
    return True


# ---------------------------------------------------------------------------
# Snippet helper
# ---------------------------------------------------------------------------

def _best_snippet(content: str, terms: list[str], max_chars: int = 120) -> str:
    """Return a short excerpt around the first term hit, with **term** bolded."""
    lower = content.lower()
    best_pos = len(content)
    for t in terms:
        pos = lower.find(t)
        if 0 <= pos < best_pos:
            best_pos = pos

    if best_pos == len(content):
        # No hit — return opening text
        return content[:max_chars].strip() + ("…" if len(content) > max_chars else "")

    start = max(0, best_pos - 40)
    end   = min(len(content), best_pos + max_chars)
    excerpt = ("…" if start > 0 else "") + content[start:end].strip() + ("…" if end < len(content) else "")
    # Mark the matched terms with ** so the UI can render them highlighted
    for t in terms:
        excerpt = re.sub(re.escape(t), f"**{t}**", excerpt, flags=re.IGNORECASE)
    return excerpt


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=None)

_login_attempts: dict[str, int] = {}
MAX_ATTEMPTS = 3


def _key(company: str, uid: str) -> str:
    return f"{company.lower()}:{uid.lower()}"


@app.get("/")
def index():
    return send_file(str(UI_PATH))


@app.get("/api/pdf/<ticker>/<path:relpath>")
def api_pdf(ticker: str, relpath: str):
    """
    Serve a PDF from any category sub-folder under DATA_ROOT.

    Requires a valid session (the HttpOnly ``session_token`` cookie; tokens in the
    URL are deliberately not accepted, as URLs end up in logs and history) and the
    signed-in user must hold <ticker> in their document_ids.
      401  no / invalid / expired session
      403  signed in, but not authorised for this ticker, or file not that ticker's
      404  unknown ticker, or file does not exist
    """
    try:
        username = validate_token(request.cookies.get("session_token", ""))
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401
    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    ticker = ticker.upper()
    if ticker not in COMPANIES:
        abort(404)
    if ticker not in user_rec.document_ids:
        _audit("pdf_denied", username, ip=request.remote_addr,
               detail={"ticker": ticker, "path": relpath})
        return jsonify(error="You are not authorised to view this company's documents.",
                       code="FORBIDDEN"), 403

    if user_rec.owners is not None and relpath.split("/", 1)[0] == REGISTER_FOLDER:
        _audit("pdf_denied", username, ip=request.remote_addr,
               detail={"ticker": ticker, "path": relpath, "reason": "owner-limited"})
        return jsonify(error="Contract registers list every department's contracts. "
                             "Use the contract analytics view instead.", code="FORBIDDEN"), 403

    if ".." in relpath:
        abort(400)
    root = DATA_ROOT.resolve()
    pdf_path = (DATA_ROOT / relpath).resolve()
    if not pdf_path.is_relative_to(root):
        abort(400)
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        abort(404)
    # The file must belong to this ticker (same rule PDFSource uses: first filename token)
    if pdf_path.stem.split("_")[0].upper() != ticker:
        _audit("pdf_denied", username, ip=request.remote_addr,
               detail={"ticker": ticker, "path": relpath})
        return jsonify(error="File does not belong to this company.", code="FORBIDDEN"), 403

    _audit("pdf_view", username, ip=request.remote_addr,
           detail={"ticker": ticker, "path": relpath})
    resp = send_file(str(pdf_path), mimetype="application/pdf")
    resp.headers["Cache-Control"] = "private, no-store"   # access-controlled: don't cache
    return resp


@app.get("/api/audit")
def api_audit():
    """Return recent audit log entries. Requires AUDIT_TOKEN header."""
    if not AUDIT_TOKEN:
        abort(403)
    auth = request.headers.get("X-Audit-Token", "")
    if auth != AUDIT_TOKEN:
        abort(403)
    n = min(int(request.args.get("n", 200)), 1000)
    return jsonify(entries=_audit_recent(n))


@app.post("/api/signin")
def api_signin():
    body     = request.get_json(force=True) or {}
    company  = (body.get("company") or "").strip()
    uid      = (body.get("id")      or "").strip()
    password = (body.get("password") or "").strip()

    if not uid or not password:
        return jsonify(error="Missing credentials.", code="BAD_CREDENTIALS"), 400

    key = _key(company, uid)
    if _login_attempts.get(key, 0) >= MAX_ATTEMPTS:
        return jsonify(error="Account locked.", code="LOCKED"), 403

    try:
        token = login(uid, password)
    except AuthError:
        _login_attempts[key] = _login_attempts.get(key, 0) + 1
        _audit("signin_fail", uid, ip=request.remote_addr, detail={"company": company})
        if _login_attempts[key] >= MAX_ATTEMPTS:
            return jsonify(error="Account locked.", code="LOCKED"), 403
        return jsonify(error="Invalid ID or password.", code="BAD_CREDENTIALS"), 401

    _login_attempts.pop(key, None)
    username = validate_token(token)
    _audit("signin", username, ip=request.remote_addr, detail={"company": company})

    # Derive display name
    user_rec = db_users.get_user(username)
    display_name = getattr(user_rec, "display_name", None) or username

    resp = jsonify(name=display_name, id=uid, company=company, token=token)
    resp.set_cookie("session_token", token, httponly=True, samesite="Strict")
    return resp


@app.post("/api/refresh")
def api_refresh():
    """Issue a fresh token if the current one is still valid; invalidate the old one."""
    body  = request.get_json(force=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")

    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401

    logout(token)
    new_token = issue_token(username)
    _audit("refresh", username, ip=request.remote_addr)

    resp = jsonify(token=new_token)
    resp.set_cookie("session_token", new_token, httponly=True, samesite="Strict")
    return resp


@app.post("/api/retrieve")
def api_retrieve():
    body  = request.get_json(force=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    query = (body.get("query") or "").strip()

    if not query:
        return jsonify(error="No query provided."), 400

    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401

    # Rate limit check
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"query": query})
        return jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429

    _audit("retrieve", username, ip=request.remote_addr, detail={"query": query})

    # Determine which tickers this user has access to
    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    allowed_tickers: set[str] = set(user_rec.document_ids)

    scoped_sources = [_sources[t] for t in allowed_tickers if t in _sources]
    if not scoped_sources:
        return jsonify(rows=[]), 200

    scoped_retriever = Retriever(scoped_sources, cache=TTLCache(ttl=300))
    docs = scoped_retriever.retrieve(Query(query, limit=50))
    if user_rec.owners is not None:     # owner-limited: hide all-department register PDFs
        docs = [d for d in docs if d.metadata.get("category") != REGISTER_CATEGORY]

    if not docs:
        return jsonify(rows=[]), 200

    terms: list[str] = [t for t in query.lower().split()]

    # Dedupe: keep the best-scoring page per report stem
    best: dict[str, Document] = {}
    for d in docs:
        stem = d.metadata.get("stem") or d.id
        if stem not in best or d.score > best[stem].score:
            best[stem] = d

    rows = []
    for d in best.values():
        meta = d.metadata
        ticker       = meta.get("ticker", "")
        period       = meta.get("period", "")
        report_type  = meta.get("report_type", "")
        category     = meta.get("category", "")
        company_name = COMPANIES.get(ticker, {}).get("name", ticker)
        total_pages  = meta.get("total_pages", "—")

        snippet = _best_snippet(d.content, terms)

        # Build a relative path from DATA_ROOT so the URL works for any sub-folder
        try:
            rel = Path(meta["path"]).relative_to(DATA_ROOT)
            file_url = f"/api/pdf/{ticker}/{rel.as_posix()}"
        except ValueError:
            file_url = ""

        label = f"{company_name}: {period} {report_type}".strip(": ")
        rows.append({
            "report":   label,
            "ticker":   ticker,
            "category": category or report_type,
            "period":   period,
            "pages":    str(total_pages),
            "snippet":  snippet,
            "status":   "Available",
            "file":     file_url,
        })

    # Top result note
    top = next(iter(best.values()))
    note = {
        "tag":   f"{top.metadata.get('ticker','')} · {top.metadata.get('period','')}",
        "title": (top.metadata.get("stem") or top.id).replace("_", " "),
        "body":  _best_snippet(top.content, terms, max_chars=200),
    }

    return jsonify(
        title="Quarterly Reports",
        subtitle=query,
        columns=COLUMNS,
        rows=rows,
        note=note,
    )


@app.post("/api/analyze/contracts")
def api_analyze_contracts():
    """
    Expiry-exposure statistics over supplier contracts.

    Body (all optional): ticker, status (Active|Expired|Renewed|Terminated|all, default Active),
    quarter (Q4-2026 | current | all) or expiry_from/expiry_to (YYYY-MM-DD), category, owner.

    The caller's scope is applied inside the query: only their tickers, and only their
    owners when the account is department-limited.  Asking for anything outside the scope
    is a 403, never a silent widening.
    """
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "analyze"})
        return jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429

    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    try:
        f = contracts.parse_filters(body)
    except ValueError as e:
        return jsonify(error=str(e), code="BAD_REQUEST"), 400

    tickers = set(user_rec.document_ids) & set(COMPANIES)
    owners  = None if user_rec.owners is None else set(user_rec.owners)
    if f.ticker and f.ticker not in tickers:
        _audit("analyze_denied", username, ip=request.remote_addr, detail={"ticker": f.ticker})
        return jsonify(error="You are not authorised to view this company's contracts.",
                       code="FORBIDDEN"), 403
    if f.owner and owners is not None and f.owner not in owners:
        _audit("analyze_denied", username, ip=request.remote_addr, detail={"owner": f.owner})
        return jsonify(error="You are not authorised to view this department's contracts.",
                       code="FORBIDDEN"), 403

    result = contracts.analyze(tickers, owners, f)
    result["scope"] = {
        "companies": [{"ticker": t, "name": COMPANIES[t]["name"]} for t in sorted(tickers)],
        "owners": None if owners is None else sorted(owners),     # None = all departments
    }
    _audit("analyze", username, ip=request.remote_addr,
           detail={"filters": result["filters"], "contracts": result["kpis"]["contracts"]})
    return jsonify(result)


@app.post("/api/analyze/financials")
def api_analyze_financials():
    """
    Growth, margin, trend and peer-comparison statistics from the quarterly reports.

    Body (all optional): ticker, from / to (Q2-2026 style), metric (revenue | gross_margin |
    operating_margin | net_margin | net_income | eps | free_cash_flow | fcf_margin).

    Scope is the caller's tickers, the same rule that governs the quarterly-report PDFs.
    Other companies are neither returned nor used in any comparison or ranking; asking
    for one by name is a 403.
    """
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "analyze_financials"})
        return jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429

    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    try:
        f = financials.parse_filters(body)
    except ValueError as e:
        return jsonify(error=str(e), code="BAD_REQUEST"), 400

    tickers = set(user_rec.document_ids) & set(COMPANIES)
    if f.ticker and f.ticker not in tickers:
        _audit("analyze_denied", username, ip=request.remote_addr, detail={"ticker": f.ticker, "kind": "financials"})
        return jsonify(error="You are not authorised to view this company's financials.",
                       code="FORBIDDEN"), 403

    result = financials.analyze(tickers, f)
    result["scope"] = {"companies": [{"ticker": t, "name": COMPANIES[t]["name"]} for t in sorted(tickers)],
                       "periods": financials.scope_periods(tickers)}
    _audit("analyze", username, ip=request.remote_addr,
           detail={"kind": "financials", "filters": result["filters"],
                   "companies": [c["ticker"] for c in result["companies"]]})
    return jsonify(result)


@app.post("/api/analyze/behavior")
def api_analyze_behavior():
    """
    Year-over-year customer-behavior scorecard from the customer-behavior reports.

    Body (all optional): ticker, year (2024 | 2025, default latest), assessment
    (all | improved | worsened | flat).

    Scope is the caller's tickers, the same rule that governs the behavior-report PDFs.
    Other companies are neither returned nor used in the peer comparison; asking for
    one by name is a 403.
    """
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "analyze_behavior"})
        return jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429

    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    try:
        f = behavior.parse_filters(body)
    except ValueError as e:
        return jsonify(error=str(e), code="BAD_REQUEST"), 400

    tickers = set(user_rec.document_ids) & set(COMPANIES)
    if f.ticker and f.ticker not in tickers:
        _audit("analyze_denied", username, ip=request.remote_addr, detail={"ticker": f.ticker, "kind": "behavior"})
        return jsonify(error="You are not authorised to view this company's customer behavior.",
                       code="FORBIDDEN"), 403

    result = behavior.analyze(tickers, f)
    result["scope"] = {"companies": [{"ticker": t, "name": COMPANIES[t]["name"]} for t in sorted(tickers)],
                       "years": behavior.scope_years(tickers)}
    _audit("analyze", username, ip=request.remote_addr,
           detail={"kind": "behavior", "filters": result["filters"],
                   "companies": [c["ticker"] for c in result["companies"]]})
    return jsonify(result)


@app.post("/api/signout")
def api_signout():
    body  = request.get_json(force=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    username = ""
    if token:
        try:
            username = validate_token(token)
        except AuthError:
            pass
        logout(token)
    _audit("signout", username, ip=request.remote_addr)
    resp = jsonify(ok=True)
    resp.delete_cookie("session_token")
    return resp


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5000)
