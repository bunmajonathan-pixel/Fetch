"""
test_server.py — Flask route tests using the built-in test client.

Covers: sign-in validation, lockout, token auth on retrieve,
        rate limiting, /api/pdf path traversal, /api/refresh, /api/signout.
"""
import pytest
import sys
import os

# Ensure retrieval_framework is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as srv
import retrieval.auth as _auth_mod


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    # Reset all stateful counters between tests
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _signin(client, uid="NL-2048", password="password1234"):
    return client.post("/api/signin", json={"id": uid, "password": password})


def _token(client):
    r = _signin(client)
    return r.get_json()["token"]


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

def test_signin_success(client):
    r = _signin(client)
    assert r.status_code == 200
    d = r.get_json()
    assert "token" in d
    assert d["id"] == "NL-2048"


def test_signin_wrong_password(client):
    r = _signin(client, password="wrong")
    assert r.status_code == 401
    assert r.get_json()["code"] == "BAD_CREDENTIALS"


def test_signin_missing_fields(client):
    r = client.post("/api/signin", json={"id": "NL-2048"})
    assert r.status_code == 400


def test_signin_lockout(client):
    for _ in range(3):
        _signin(client, password="bad")
    r = _signin(client, password="password1234")   # correct password but locked
    assert r.status_code == 403
    assert r.get_json()["code"] == "LOCKED"


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

def test_retrieve_no_token(client):
    r = client.post("/api/retrieve", json={"query": "revenue"})
    assert r.status_code == 401


def test_retrieve_bad_token(client):
    r = client.post("/api/retrieve", json={"query": "revenue", "token": "bad"})
    assert r.status_code == 401


def test_retrieve_valid(client):
    token = _token(client)
    r = client.post("/api/retrieve", json={"query": "revenue", "token": token})
    # May be 200 with rows or 200 with empty rows — must not be 4xx
    assert r.status_code == 200


def test_retrieve_empty_query(client):
    token = _token(client)
    r = client.post("/api/retrieve", json={"query": "", "token": token})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

def test_rate_limit(client):
    token = _token(client)
    # Exhaust the rate limit
    for _ in range(srv._RATE_LIMIT):
        client.post("/api/retrieve", json={"query": "revenue", "token": token})
    r = client.post("/api/retrieve", json={"query": "revenue", "token": token})
    assert r.status_code == 429
    assert r.get_json()["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# PDF endpoint — path traversal guard
# ---------------------------------------------------------------------------

def test_pdf_traversal_blocked(client):
    r = client.get("/api/pdf/BLFD/../../../etc/passwd")
    assert r.status_code in (400, 404)


def test_pdf_unknown_ticker(client):
    r = client.get("/api/pdf/XXXX/somefile.pdf")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def test_refresh_valid(client):
    token = _token(client)
    r = client.post("/api/refresh", json={"token": token})
    assert r.status_code == 200
    new = r.get_json()["token"]
    assert new != token


def test_refresh_old_token_invalidated(client):
    token = _token(client)
    client.post("/api/refresh", json={"token": token})
    # Old token should now be rejected — delete_cookie ensures the cookie doesn't override
    client.delete_cookie("session_token")
    r = client.post("/api/retrieve", json={"query": "revenue", "token": token})
    assert r.status_code == 401


def test_refresh_bad_token(client):
    r = client.post("/api/refresh", json={"token": "invalid"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Sign-out
# ---------------------------------------------------------------------------

def test_signout_invalidates_token(client):
    token = _token(client)
    client.post("/api/signout", json={"token": token})
    r = client.post("/api/retrieve", json={"query": "revenue", "token": token})
    assert r.status_code == 401


def test_signout_no_token_ok(client):
    r = client.post("/api/signout", json={})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Supplier-contracts demo: "active supplier contracts expiring in Q4 2026"
# ---------------------------------------------------------------------------

DEMO_QUERY = "Show active supplier contracts expiring in Q4 2026"


def _rows_for(client, uid, password, query=DEMO_QUERY):
    token = _signin(client, uid, password).get_json()["token"]
    r = client.post("/api/retrieve", json={"query": query, "token": token})
    assert r.status_code == 200
    return r.get_json()["rows"]


@pytest.mark.parametrize("uid,password,ticker", [
    ("BLFD-01", "blfd2024", "BLFD"),
    ("HDYN-01", "hdyn2024", "HDYN"),
    ("MCLD-01", "mcld2024", "MCLD"),
])
def test_supplier_contracts_demo_scoped_to_user(client, uid, password, ticker):
    """Each user gets only their own company's Q4-2026 contract register."""
    rows = _rows_for(client, uid, password)
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == ticker
    assert row["period"] == "Q4-2026"
    assert row["category"] == "Supplier Contracts"
    assert row["file"] == (
        f"/api/pdf/{ticker}/Supplier_Contracts/{ticker}_Q4-2026_Supplier_Contracts.pdf"
    )


def test_supplier_contracts_period_filter(client):
    """The quarter in the query selects the matching expiry-quarter file."""
    rows = _rows_for(client, "BLFD-01", "blfd2024",
                     "supplier contracts expiring in Q1 2027")
    assert [r["period"] for r in rows] == ["Q1-2027"]


def test_supplier_contract_pdf_is_servable(client):
    rows = _rows_for(client, "HDYN-01", "hdyn2024")
    r = client.get(rows[0]["file"])
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"


def test_supplier_contract_pdf_wrong_ticker_rejected(client):
    """A BLFD URL cannot be used to fetch another company's file."""
    r = client.get("/api/pdf/BLFD/Supplier_Contracts/HDYN_Q4-2026_Supplier_Contracts.pdf")
    assert r.status_code == 403
