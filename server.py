"""
server.py — Flask HTTP server bridging the browser UI (ui/index.html) to the PDF retrieval backend.

Endpoints
---------
GET  /               → serves ui/index.html
POST /api/signin     { company, id, password }  → { name, id, company, token }
POST /api/retrieve   { query, token }           → { title, subtitle, columns, rows, note? }
POST /api/refresh    { token }                  → { token }   (new token, old one invalidated)
POST /api/signout    { token }                  → { ok: true }
GET  /api/pdf/<ticker>/<filename>               → PDF file
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
from retrieval import db_users

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
    """Serve a PDF from any category sub-folder under DATA_ROOT."""
    ticker = ticker.upper()
    if ticker not in COMPANIES:
        abort(404)
    if ".." in relpath:
        abort(400)
    pdf_path = DATA_ROOT / relpath
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        abort(404)
    # Verify the file actually belongs to this ticker
    if not pdf_path.stem.upper().startswith(ticker):
        abort(403)
    return send_file(str(pdf_path), mimetype="application/pdf")


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

    print(f"[signin] company={company!r} id={uid!r} password_len={len(password)} "
          f"user_found={db_users.get_user(uid) is not None}", flush=True)

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
