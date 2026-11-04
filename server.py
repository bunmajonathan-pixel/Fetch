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
import hmac
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
from retrieval.auth import issue_token, revoke_user
from retrieval.sources import PDFSource
from retrieval.types import Query, Document
from retrieval.audit import configure as _audit_configure, record as _audit, recent as _audit_recent
from retrieval import alerts, behavior, company, contracts, db_users, financials, views

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
    {"key": "report",    "label": "Report",        "w": "31%"},
    {"key": "ticker",    "label": "Ticker",        "w": "7%"},
    {"key": "category",  "label": "Category",      "w": "16%"},
    {"key": "period",    "label": "Period",        "w": "9%"},
    {"key": "pages",     "label": "Pages",         "w": "6%"},
    {"key": "snippet",   "label": "Matched text",  "w": "14%"},
    {"key": "status",    "label": "Status",        "w": "9%", "type": "status"},
    {"key": "file",      "label": "View",          "w": "8%", "type": "pdf_link"},
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

# Saved views (named filter sets per user), in their own file so a user-database reset keeps them
views.init_views(INSTANCE_DIR / "views.db")

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


def _key(uid: str) -> str:
    """Lockout key: the account only.  The sign-in form also sends a free-text
    "company", which an attacker controls, so it must not be part of the key."""
    return uid.lower()


@app.after_request
def _security_headers(resp):
    """Defence in depth for a page that shows access-controlled data."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")      # no framing by other sites (clickjacking)
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                            "img-src 'self' data: blob:; frame-src 'self'; object-src 'self'; "
                            "frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")      # API answers are per-user
    return resp


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
    if not hmac.compare_digest(auth.encode(), AUDIT_TOKEN.encode()):
        abort(403)
    try:
        n = max(1, min(int(request.args.get("n", 200)), 1000))
    except ValueError:
        return jsonify(error="n must be a number.", code="BAD_REQUEST"), 400
    return jsonify(entries=_audit_recent(n))


@app.post("/api/signin")
def api_signin():
    body     = request.get_json(force=True) or {}
    company  = (body.get("company") or "").strip()
    uid      = (body.get("id")      or "").strip()
    password = (body.get("password") or "").strip()

    if not uid or not password:
        return jsonify(error="Missing credentials.", code="BAD_CREDENTIALS"), 400

    key = _key(uid)
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

    resp = jsonify(name=display_name, id=uid, company=company, token=token,
                   admin=bool(getattr(user_rec, "is_admin", False)))
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


def _row_figures(ticker: str, period: str, category: str, file_name: str) -> dict | None:
    """Key numbers to show under a search hit that is a quarterly or customer-behavior report."""
    try:
        if file_name.endswith("_Quarterly_Report.pdf"):
            return financials.report_figures(ticker, period)
        if category == "behavior" and period.isdigit():
            c = next(iter(behavior.analyze({ticker}, behavior.Filters(year=int(period)))["companies"]), None)
            if c is None:
                return None
            sc = c.get("scorecard")
            return {"kind": "behavior", "period": period, "customers_pct": (c.get("growth") or {}).get("customers_pct"),
                    "revenue_pct": (c.get("growth") or {}).get("revenue_pct"),
                    "improved": sc["improved"] if sc else None, "worsened": sc["worsened"] if sc else None,
                    "flat": sc["flat"] if sc else None, "concerns": [r["label"] for r in c.get("concerns", [])][:3]}
    except Exception:                       # a hit must never fail because its numbers could not be built
        return None
    return None


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
            "figures":  _row_figures(ticker, period, category, Path(meta.get("path", "")).name),
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
    operating_margin | net_margin | net_income | eps | free_cash_flow | fcf_margin),
    assessment (all | improved | worsened | flat) and basis (yoy | qoq), which choose the
    improved / worse / flat flags returned in each company's ``scorecard``.

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


def _company_profiles(tickers: set[str], owners: set[str] | None, want: str | None = None) -> list[dict]:
    """Company profiles for the caller's own scope (shared by the company page and the alert feed)."""
    profiles = []
    for t in sorted(tickers):
        if want and t != want:
            continue
        fin = financials.analyze({t}, financials.parse_filters({}))["companies"]
        beh = behavior.analyze({t}, behavior.Filters())["companies"]
        if not fin or not beh:
            continue
        con = contracts.analyze({t}, owners, contracts.parse_filters({"ticker": t}))
        profiles.append(company.profile(t, COMPANIES[t]["name"], fin[0], beh[0], con, partial=owners is not None))
    return profiles


@app.post("/api/analyze/company")
def api_analyze_company():
    """
    One page per company that joins financials, customer behavior and supplier contracts,
    with signals that need two sources to see.

    Body (optional): ticker.  Without it every company in the caller's scope is returned.

    Each source keeps its own rule.  Financials and behavior follow the caller's tickers;
    contracts also follow the caller's departments, so a department-limited account gets
    its own departments' contracts only (``partial_contracts`` is then true and signals
    that compare contract totals with company-wide numbers are left out).
    """
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "analyze_company"})
        return jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429

    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    want = (body.get("ticker") or "").strip().upper() or None
    tickers = set(user_rec.document_ids) & set(COMPANIES)
    if want and want not in tickers:
        _audit("analyze_denied", username, ip=request.remote_addr, detail={"ticker": want, "kind": "company"})
        return jsonify(error="You are not authorised to view this company.", code="FORBIDDEN"), 403

    owners   = None if user_rec.owners is None else set(user_rec.owners)
    profiles = _company_profiles(tickers, owners, want)

    _audit("analyze", username, ip=request.remote_addr,
           detail={"kind": "company", "companies": [p["ticker"] for p in profiles]})
    return jsonify({"companies": profiles,
                    "scope": {"companies": [{"ticker": t, "name": COMPANIES[t]["name"]} for t in sorted(tickers)],
                              "owners": None if owners is None else sorted(owners)}})


@app.post("/api/analyze/alerts")
def api_analyze_alerts():
    """
    One ranked "needs attention" list across the caller's companies: the company-page signals
    plus quarters whose results landed outside the range the trend implied.

    Body (optional): ticker.  Built from the same permission-scoped profiles as the company
    page, so a department-limited account sees alerts from its own departments' contracts only.
    """
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return jsonify(error=str(e), code="UNAUTHORIZED"), 401
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "analyze_alerts"})
        return jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429
    user_rec = db_users.get_user(username)
    if user_rec is None:
        return jsonify(error="User record not found.", code="UNAUTHORIZED"), 401

    want = (body.get("ticker") or "").strip().upper() or None
    tickers = set(user_rec.document_ids) & set(COMPANIES)
    if want and want not in tickers:
        _audit("analyze_denied", username, ip=request.remote_addr, detail={"ticker": want, "kind": "alerts"})
        return jsonify(error="You are not authorised to view this company.", code="FORBIDDEN"), 403

    owners = None if user_rec.owners is None else set(user_rec.owners)
    profiles = _company_profiles(tickers, owners, want)
    result = alerts.build(profiles)
    _audit("analyze", username, ip=request.remote_addr,
           detail={"kind": "alerts", "companies": [p["ticker"] for p in profiles], "watch": result["counts"]["watch"]})
    return jsonify({**result, "scope": {"companies": [{"ticker": t, "name": COMPANIES[t]["name"]} for t in sorted(tickers)],
                                         "owners": None if owners is None else sorted(owners)}})


def _signed_in():
    """(username, None) for a valid, un-throttled session, else (None, error response)."""
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return None, (jsonify(error=str(e), code="UNAUTHORIZED"), 401)
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "views"})
        return None, (jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429)
    return username, None


def _check_view_filters(screen: str, filters: dict) -> None:
    """Run the screen's own filter parser, so only filters it would accept can be saved."""
    if screen == "contracts":
        contracts.parse_filters(filters)
    elif screen == "financials":
        financials.parse_filters(filters)
    elif screen == "behavior":
        behavior.parse_filters(filters)
    elif screen == "company" and filters.get("ticker", "").upper() not in ("", *COMPANIES):
        raise ValueError("Unknown company.")


@app.get("/api/views")
def api_views_list():
    username, err = _signed_in()
    if err:
        return err
    return jsonify(views=views.list_views(username))


@app.post("/api/views")
def api_views_save():
    """Body: { name, screen, filters }.  Saving a name that already exists on that screen replaces it."""
    username, err = _signed_in()
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    try:
        name, filters = views.clean(body.get("screen"), body.get("name"), body.get("filters") or {})
        _check_view_filters(body["screen"], filters)
        saved = views.save(username, body["screen"], name, filters)
    except ValueError as e:
        return jsonify(error=str(e), code="BAD_REQUEST"), 400
    _audit("view_saved", username, ip=request.remote_addr,
           detail={"screen": saved["screen"], "name": saved["name"]})
    return jsonify(saved), 201


@app.delete("/api/views/<int:view_id>")
def api_views_delete(view_id: int):
    username, err = _signed_in()
    if err:
        return err
    if not views.delete(username, view_id):
        return jsonify(error="View not found.", code="NOT_FOUND"), 404
    _audit("view_deleted", username, ip=request.remote_addr, detail={"id": view_id})
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Administration: accounts and the audit log (administrators only)
# ---------------------------------------------------------------------------
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,31}$")
MIN_PASSWORD = 10


def _require_admin():
    """(admin_username, token, None) for an administrator, else (None, None, error response)."""
    body  = request.get_json(force=True, silent=True) or {}
    token = request.cookies.get("session_token") or body.get("token", "")
    try:
        username = validate_token(token)
    except AuthError as e:
        return None, None, (jsonify(error=str(e), code="UNAUTHORIZED"), 401)
    if not _check_rate(token):
        _audit("rate_limited", username, ip=request.remote_addr, detail={"endpoint": "admin"})
        return None, None, (jsonify(error="Too many requests. Please wait a moment.", code="RATE_LIMITED"), 429)
    rec = db_users.get_user(username)
    if rec is None or not getattr(rec, "is_admin", False):
        _audit("admin_denied", username, ip=request.remote_addr, detail={"path": request.path})
        return None, None, (jsonify(error="Administrator access is required.", code="FORBIDDEN"), 403)
    return username, token, None


def _departments_for(tickers: list[str]) -> dict[str, list[str]]:
    return {t: contracts.scope_options({t}, None)["owners"] for t in tickers}


def _is_locked(username: str) -> bool:
    return _login_attempts.get(_key(username), 0) >= MAX_ATTEMPTS


def _account_json(rec) -> dict:
    return {"username": rec.username, "document_ids": list(rec.document_ids),
            "owners": None if rec.owners is None else list(rec.owners),
            "is_admin": bool(rec.is_admin), "locked": _is_locked(rec.username),
            "sessions": sum(1 for u in _active_sessions().values() if u == rec.username)}


def _active_sessions() -> dict:
    from retrieval.auth import active_sessions
    return active_sessions()


def _clean_access(body: dict, current=None):
    """Validate document_ids / owners.  Returns (document_ids, owners) with owners as given (None = no limit)."""
    ids = body.get("document_ids", None if current is None else current.document_ids)
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise ValueError("document_ids must be a list of company tickers.")
    ids = sorted({i.strip().upper() for i in ids})
    bad = [i for i in ids if i not in COMPANIES]
    if bad:
        raise ValueError(f"Unknown company: {', '.join(bad)}. Choose from {', '.join(COMPANIES)}.")
    if "owners" in body:
        owners = body["owners"]
    else:
        owners = None if current is None else current.owners
    if owners is not None:
        if not isinstance(owners, list) or any(not isinstance(o, str) for o in owners):
            raise ValueError("owners must be a list of department names, or null for no limit.")
        valid = {o for t in ids for o in _departments_for([t])[t]}
        bad = sorted(set(owners) - valid)
        if bad:
            raise ValueError(f"Unknown department for these companies: {', '.join(bad)}.")
        owners = sorted(set(owners))
    return ids, owners


def _clean_password(pw, username: str) -> str:
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise ValueError(f"The password must be at least {MIN_PASSWORD} characters.")
    if len(pw) > 128:
        raise ValueError("The password is too long.")
    if pw.lower() == username.lower():
        raise ValueError("The password must not be the same as the username.")
    return pw


@app.get("/api/admin/users")
def api_admin_users():
    admin, _, err = _require_admin()
    if err:
        return err
    return jsonify(users=[_account_json(r) for r in db_users.list_users()],
                   options={"companies": [{"ticker": t, "name": c["name"]} for t, c in COMPANIES.items()],
                            "departments": _departments_for(list(COMPANIES)), "min_password": MIN_PASSWORD})


@app.post("/api/admin/users")
def api_admin_create_user():
    admin, _, err = _require_admin()
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    username = str(body.get("username") or "").strip()
    try:
        if not _USERNAME_RE.match(username):
            raise ValueError("The username must be 2 to 32 letters, numbers, dots, dashes or underscores.")
        pw = _clean_password(body.get("password"), username)
        ids, owners = _clean_access({"document_ids": body.get("document_ids", []), "owners": body.get("owners")})
        rec = db_users.register_user(username, pw, ids, owners, is_admin=bool(body.get("is_admin")))
    except ValueError as e:
        return jsonify(error=str(e), code="BAD_REQUEST"), 400
    _audit("admin_user_created", admin, ip=request.remote_addr,
           detail={"user": username, "document_ids": ids, "owners": owners, "is_admin": rec.is_admin})
    return jsonify(_account_json(rec)), 201


@app.patch("/api/admin/users/<username>")
def api_admin_update_user(username: str):
    """Body (all optional): document_ids, owners (null = no limit), password, is_admin."""
    admin, token, err = _require_admin()
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    cur = db_users.get_user(username)
    if cur is None:
        return jsonify(error="User not found.", code="NOT_FOUND"), 404
    changes: dict = {}
    try:
        if "document_ids" in body or "owners" in body:
            ids, owners = _clean_access(body, cur)
            changes["document_ids"], changes["owners"] = ids, owners
        if "password" in body:
            changes["password"] = _clean_password(body["password"], username)
        if "is_admin" in body:
            want = bool(body["is_admin"])
            if not want and cur.is_admin and (username == admin or db_users.count_admins() <= 1):
                raise ValueError("You cannot remove your own administrator rights or those of the last administrator.")
            changes["is_admin"] = want
        if not changes:
            raise ValueError("Nothing to change.")
    except ValueError as e:
        return jsonify(error=str(e), code="BAD_REQUEST"), 400
    rec = db_users.update_user(username, **changes)
    ended = 0
    if "password" in changes or changes.get("is_admin") is False:
        ended = revoke_user(username, keep=token if username == admin else None)
    _audit("admin_user_updated", admin, ip=request.remote_addr,
           detail={"user": username, "changed": sorted(changes), "sessions_ended": ended,
                   **{k: v for k, v in changes.items() if k in ("document_ids", "owners", "is_admin")}})
    return jsonify(_account_json(rec))


@app.delete("/api/admin/users/<username>")
def api_admin_delete_user(username: str):
    admin, _, err = _require_admin()
    if err:
        return err
    cur = db_users.get_user(username)
    if cur is None:
        return jsonify(error="User not found.", code="NOT_FOUND"), 404
    if username == admin:
        return jsonify(error="You cannot delete your own account.", code="BAD_REQUEST"), 400
    if cur.is_admin and db_users.count_admins() <= 1:
        return jsonify(error="You cannot delete the last administrator.", code="BAD_REQUEST"), 400
    db_users.delete_user(username)
    ended = revoke_user(username)
    for v in views.list_views(username):
        views.delete(username, v["id"])
    _audit("admin_user_deleted", admin, ip=request.remote_addr, detail={"user": username, "sessions_ended": ended})
    return jsonify(ok=True)


@app.post("/api/admin/users/<username>/unlock")
def api_admin_unlock_user(username: str):
    admin, _, err = _require_admin()
    if err:
        return err
    if db_users.get_user(username) is None:
        return jsonify(error="User not found.", code="NOT_FOUND"), 404
    _login_attempts.pop(_key(username), None)
    _audit("admin_user_unlocked", admin, ip=request.remote_addr, detail={"user": username})
    return jsonify(ok=True)


@app.get("/api/admin/audit")
def api_admin_audit():
    """Recent audit entries, newest first.  Query: user, event, n (default 200, max 1000)."""
    admin, _, err = _require_admin()
    if err:
        return err
    try:
        n = max(1, min(int(request.args.get("n", 200)), 1000))
    except ValueError:
        return jsonify(error="n must be a number.", code="BAD_REQUEST"), 400
    user  = (request.args.get("user") or "").strip().lower()
    event = (request.args.get("event") or "").strip()
    pool  = _audit_recent(1000)
    entries = [e for e in pool if (not user or e["username"].lower() == user) and (not event or e["event"] == event)]
    # Accounts with repeated refusals or failed sign-ins, over everything in the buffer
    bad = {"analyze_denied": "denied", "pdf_denied": "denied", "admin_denied": "denied",
           "signin_fail": "failed_signins", "rate_limited": "rate_limited"}
    flags: dict[str, dict[str, int]] = {}
    for e in pool:
        if e["event"] in bad:
            f = flags.setdefault(e["username"] or "(unknown)", {"denied": 0, "failed_signins": 0, "rate_limited": 0})
            f[bad[e["event"]]] += 1
    flagged = sorted(({"username": u, **f, "total": sum(f.values())} for u, f in flags.items() if sum(f.values()) >= 3),
                     key=lambda r: -r["total"])
    return jsonify(entries=list(reversed(entries))[:n], events=sorted({e["event"] for e in pool}),
                   flagged=flagged, total=len(pool))


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
    # The Werkzeug debugger allows code execution, so it is opt-in: FETCH_DEBUG=1 python server.py
    app.run(debug=os.environ.get("FETCH_DEBUG") == "1", use_reloader=False, port=5000)
