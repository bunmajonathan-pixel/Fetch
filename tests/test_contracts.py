"""
test_contracts.py — contract analytics: dataset, statistics, permission scoping
(company-level AND record-level), and the API/UI-facing behaviour around them.
"""
import csv
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

import retrieval.auth as _auth_mod
import server as srv
from retrieval import contracts, db_users
from retrieval.contracts import Filters

CSV_PATH = srv.CONTRACTS_CSV
DATA = srv.DATA_ROOT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _login(client, uid, pw):
    r = client.post("/api/signin", json={"id": uid, "password": pw})
    assert r.status_code == 200
    return r.get_json()["token"]


def _analyze(client, uid, pw, **body):
    _login(client, uid, pw)
    return client.post("/api/analyze/contracts", json=body)


FULL = ("BLFD-01", "blfd2024")
LIMITED = ("BLFD-LOG", "blfdlog2024")


@pytest.fixture()
def tiny_db(tmp_path):
    """A 6-row contracts DB for exact-arithmetic tests; restores the real DB afterwards."""
    rows = [
        # id, ticker, supplier, category, owner, start, expiry, status, value
        ("T-1", "AAA", "Alpha", "Cat1", "Ops", "2024-01-01", "2026-11-14", "Active", 1000),   # +10d
        ("T-2", "AAA", "Alpha", "Cat1", "Ops", "2024-01-01", "2026-12-04", "Active", 3000),   # +30d
        ("T-3", "AAA", "Beta",  "Cat2", "Fin", "2024-01-01", "2026-12-31", "Active", 2000),   # +57d
        ("T-4", "AAA", "Gamma", "Cat2", "Fin", "2024-01-01", "2026-10-15", "Expired", 500),   # -20d
        ("T-5", "AAA", "Delta", "Cat1", "Ops", "2024-01-01", "2027-12-31", "Active", 4000),   # later (>12-month span)
        ("T-6", "BBB", "Omega", "Cat1", "Ops", "2024-01-01", "2026-12-01", "Active", 9999),   # other company
    ]
    path = tmp_path / "c.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(contracts._COLUMNS)
        w.writerows(rows)
    old = contracts._DB_PATH
    contracts.init_contracts(tmp_path / "c.db", path)
    yield path
    contracts._DB_PATH = old


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def test_dataset_loaded_and_consistent():
    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 300
    assert len({r["contract_id"] for r in rows}) == len(rows)
    assert {r["ticker"] for r in rows} == {"BLFD", "HDYN", "MCLD"}
    for r in rows:
        assert r["status"] in contracts.STATUSES
        exp = date.fromisoformat(r["expiry_date"])
        assert date.fromisoformat(r["start_date"]) < exp
        if r["status"] == "Active":
            assert exp >= contracts.AS_OF, r
        if r["status"] in ("Expired", "Renewed"):
            assert exp < contracts.AS_OF, r
    all_rows = contracts.fetch_contracts({"BLFD", "HDYN", "MCLD"}, None, Filters(status=None))
    assert len(all_rows) == len(rows)


@pytest.mark.parametrize("ticker", ["BLFD", "HDYN", "MCLD"])
def test_analytics_agree_with_register_pdf(ticker):
    """The Q4-2026 register PDF and the analytics must report the same totals."""
    pdf = DATA / "Supplier_Contracts" / f"{ticker}_Q4-2026_Supplier_Contracts.pdf"
    text = PdfReader(str(pdf)).pages[0].extract_text()
    m = re.search(r"(\d+) active supplier contracts expire in Q4 2026, with a combined "
                  r"annual value of \$([\d,]+)K", text)
    count, value = int(m.group(1)), int(m.group(2).replace(",", ""))
    f = contracts.parse_filters({"quarter": "Q4-2026", "status": "Active"})
    k = contracts.analyze({ticker}, None, f)["kpis"]
    assert (k["contracts"], k["value_k"]) == (count, value)


def test_csv_change_reloads_database(tmp_path):
    path = tmp_path / "c.csv"
    row = ["T-1", "AAA", "S", "C", "O", "2024-01-01", "2026-12-01", "Active", "100"]
    old = contracts._DB_PATH
    try:
        def write(rows):
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(contracts._COLUMNS)
                w.writerows(rows)
        write([row])
        assert contracts.init_contracts(tmp_path / "c.db", path) == 1
        assert contracts.init_contracts(tmp_path / "c.db", path) == 1        # unchanged: no reload
        write([row, ["T-2"] + row[1:]])
        assert contracts.init_contracts(tmp_path / "c.db", path) == 2        # changed: reloaded
    finally:
        contracts._DB_PATH = old


# ---------------------------------------------------------------------------
# Filters and quarters
# ---------------------------------------------------------------------------

def test_quarter_bounds():
    assert contracts.quarter_bounds("Q4-2026") == (date(2026, 10, 1), date(2026, 12, 31))
    assert contracts.quarter_bounds("Q1-2027") == (date(2027, 1, 1), date(2027, 3, 31))
    assert contracts.quarter_of(date(2026, 11, 4)) == "Q4-2026"
    with pytest.raises(ValueError):
        contracts.quarter_bounds("Q5-2026")


def test_parse_filters_defaults_and_current():
    f = contracts.parse_filters({})
    assert f.status == "Active" and f.expiry_from is None and f.ticker is None
    f = contracts.parse_filters({"quarter": "current", "status": "all", "ticker": "blfd"})
    assert (f.expiry_from, f.expiry_to) == (date(2026, 10, 1), date(2026, 12, 31))
    assert f.status is None and f.ticker == "BLFD"
    assert contracts.parse_filters({"status": "expired"}).status == "Expired"


@pytest.mark.parametrize("body", [
    {"status": "bogus"},
    {"quarter": "Q9-2026"},
    {"quarter": "Q4-2026", "expiry_from": "2026-10-01"},
    {"expiry_from": "2026-12-31", "expiry_to": "2026-01-01"},
    {"expiry_from": "not-a-date"},
    {"owner": ["Logistics"]},
    {"category": "x" * 101},
])
def test_parse_filters_rejects_bad_input(body):
    with pytest.raises(ValueError):
        contracts.parse_filters(body)


# ---------------------------------------------------------------------------
# Statistics (exact arithmetic on a tiny dataset)
# ---------------------------------------------------------------------------

def test_statistics_exact(tiny_db):
    f = contracts.parse_filters({"quarter": "Q4-2026", "status": "Active"})
    a = contracts.analyze({"AAA"}, None, f)
    k = a["kpis"]
    assert (k["contracts"], k["value_k"]) == (3, 6000)               # T-1, T-2, T-3
    assert k["active_spend_k"] == 10000                              # + T-5; T-6 is company BBB
    assert k["exposure_pct"] == 60.0
    assert k["top_supplier"]["name"] == "Alpha" and k["top_supplier"]["value_k"] == 4000
    assert k["top3_share_pct"] == 100.0
    assert k["expiring_30d"] == {"contracts": 2, "value_k": 4000}    # +10d and +30d
    assert k["avg_days_to_expiry"] == round((10 + 30 + 57) / 3)
    assert [(g["name"], g["value_k"], g["share_pct"]) for g in a["by_category"]] == \
        [("Cat1", 4000, 66.7), ("Cat2", 2000, 33.3)]
    # Oct/Nov/Dec zero-filled: nothing active expires in October
    assert [(p["label"], p["contracts"]) for p in a["timeline"]["points"]] == \
        [("Oct 2026", 0), ("Nov 2026", 1), ("Dec 2026", 2)]
    assert {b["label"]: b["contracts"] for b in a["buckets"]}["0–30 days"] == 2
    assert {b["label"]: b["contracts"] for b in a["buckets"]}["31–60 days"] == 1


def test_exposure_only_for_active_status(tiny_db):
    a = contracts.analyze({"AAA"}, None, contracts.parse_filters({"status": "all"}))
    assert a["kpis"]["exposure_pct"] is None
    assert a["kpis"]["contracts"] == 5          # BBB excluded by the ticker scope


def test_quarterly_timeline_for_long_windows(tiny_db):
    a = contracts.analyze({"AAA"}, None, contracts.parse_filters({"status": "all"}))
    assert a["timeline"]["unit"] == "quarter"
    assert a["timeline"]["points"][0]["label"] == "Q4 2026"
    assert a["timeline"]["points"][-1]["label"] == "Q4 2027"
    assert len(a["timeline"]["points"]) == 5          # Q4 2026 .. Q4 2027, zero-filled


def test_empty_result_is_well_formed(tiny_db):
    a = contracts.analyze({"AAA"}, None, contracts.parse_filters({"category": "Nope"}))
    assert a["kpis"]["contracts"] == 0 and a["kpis"]["top_supplier"] is None
    assert a["rows"] == [] and a["timeline"]["points"] == []


# ---------------------------------------------------------------------------
# Scope is enforced inside the query
# ---------------------------------------------------------------------------

def test_scope_limits_tickers_and_owners(tiny_db):
    everything = Filters(status=None)
    assert {r["ticker"] for r in contracts.fetch_contracts({"AAA"}, None, everything)} == {"AAA"}
    assert {r["owner"] for r in contracts.fetch_contracts({"AAA"}, {"Fin"}, everything)} == {"Fin"}
    assert contracts.fetch_contracts({"AAA"}, set(), everything) == []      # no owners = nothing
    assert contracts.fetch_contracts(set(), None, everything) == []         # no tickers = nothing


def test_user_filters_cannot_widen_scope(tiny_db):
    """Asking for another company / department via filters returns nothing, never more."""
    assert contracts.fetch_contracts({"AAA"}, None, Filters(status=None, ticker="BBB")) == []
    assert contracts.fetch_contracts({"AAA"}, {"Fin"}, Filters(status=None, owner="Ops")) == []
    opts = contracts.scope_options({"AAA"}, {"Fin"})
    assert opts["owners"] == ["Fin"] and opts["categories"] == ["Cat2"]


# ---------------------------------------------------------------------------
# API — authentication, company scope, record-level scope
# ---------------------------------------------------------------------------

def test_api_requires_signin(client):
    r = client.post("/api/analyze/contracts", json={})
    assert r.status_code == 401


def test_api_token_in_body_works(client):
    token = _login(client, *FULL)
    client.delete_cookie("session_token")
    r = client.post("/api/analyze/contracts", json={"token": token, "quarter": "current"})
    assert r.status_code == 200


def test_api_company_scope(client):
    j = _analyze(client, *FULL, quarter="Q4-2026").get_json()
    assert [c["ticker"] for c in j["scope"]["companies"]] == ["BLFD"]
    assert {r["ticker"] for r in j["rows"]} == {"BLFD"} and j["scope"]["owners"] is None
    assert (j["kpis"]["contracts"], j["kpis"]["value_k"]) == (5, 10770)

    j = _analyze(client, "NL-2048", "password1234", quarter="Q4-2026").get_json()
    assert {r["ticker"] for r in j["rows"]} == {"BLFD", "HDYN", "MCLD"}
    assert [g["name"] for g in j["by_ticker"]] and len(j["by_ticker"]) == 3


def test_api_other_company_forbidden_and_audited(client):
    r = _analyze(client, *FULL, ticker="HDYN")
    assert r.status_code == 403 and r.get_json()["code"] == "FORBIDDEN"
    from retrieval.audit import recent
    assert any(e["event"] == "analyze_denied" and e["username"] == "BLFD-01" for e in recent(50))


def test_api_user_without_company_access_gets_empty_scope(client):
    j = _analyze(client, "alice", "alice123").get_json()      # document_ids are test IDs, not tickers
    assert j["scope"]["companies"] == [] and j["kpis"]["contracts"] == 0 and j["rows"] == []


def test_api_bad_request(client):
    r = _analyze(client, *FULL, status="nonsense")
    assert r.status_code == 400 and r.get_json()["code"] == "BAD_REQUEST"


def test_record_level_split_same_company_different_totals(client):
    full = _analyze(client, *FULL, quarter="Q4-2026").get_json()
    lim = _analyze(client, *LIMITED, quarter="Q4-2026").get_json()
    assert lim["scope"]["owners"] == ["Logistics"] and full["scope"]["owners"] is None
    assert {r["owner"] for r in lim["rows"]} == {"Logistics"}
    assert (lim["kpis"]["contracts"], lim["kpis"]["value_k"]) == (2, 6110)
    assert lim["kpis"]["value_k"] < full["kpis"]["value_k"]
    # and the denominator is the department's own active spend, not the company's
    assert lim["kpis"]["active_spend_k"] < full["kpis"]["active_spend_k"]


def test_record_level_nothing_from_other_departments_leaks(client):
    """No supplier, owner or contract from outside Logistics appears anywhere in the response."""
    others = [r for r in contracts.fetch_contracts({"BLFD"}, None, Filters(status=None))
              if r["owner"] != "Logistics"]
    assert others
    hidden = {r["supplier"] for r in others} - \
             {r["supplier"] for r in contracts.fetch_contracts({"BLFD"}, {"Logistics"}, Filters(status=None))}
    hidden |= {r["contract_id"] for r in others} | {"Procurement", "Operations", "Quality"}
    for body in ({}, {"quarter": "Q4-2026"}, {"status": "all"}, {"status": "all", "quarter": "all"}):
        blob = json.dumps(_analyze(client, *LIMITED, **body).get_json())
        for token in hidden:
            assert token not in blob, f"{token!r} leaked for {body}"


def test_record_level_other_department_forbidden(client):
    r = _analyze(client, *LIMITED, owner="Quality")
    assert r.status_code == 403
    assert _analyze(client, *LIMITED, owner="Logistics").status_code == 200


# ---------------------------------------------------------------------------
# Record-level limit also covers the register PDFs and search
# ---------------------------------------------------------------------------

REGISTER = "/api/pdf/BLFD/Supplier_Contracts/BLFD_Q4-2026_Supplier_Contracts.pdf"
REPORT = "/api/pdf/BLFD/individual/BLFD/BLFD_Q1-2024_Quarterly_Report.pdf"
DEMO_QUERY = "Show active supplier contracts expiring in Q4 2026"


def test_limited_user_cannot_open_register_pdf(client):
    _login(client, *LIMITED)
    r = client.get(REGISTER)
    assert r.status_code == 403 and r.get_json()["code"] == "FORBIDDEN"
    assert client.get(REPORT).status_code == 200            # company-wide reports are still fine


def test_unlimited_user_can_open_register_pdf(client):
    _login(client, *FULL)
    assert client.get(REGISTER).status_code == 200


def test_limited_user_search_hides_register_documents(client):
    _login(client, *FULL)
    assert len(client.post("/api/retrieve", json={"query": DEMO_QUERY}).get_json()["rows"]) == 1
    _login(client, *LIMITED)
    assert client.post("/api/retrieve", json={"query": DEMO_QUERY}).get_json()["rows"] == []


# ---------------------------------------------------------------------------
# User store: owners column
# ---------------------------------------------------------------------------

def test_default_accounts_carry_owner_limits():
    assert db_users.get_user("BLFD-LOG").owners == ["Logistics"]
    assert db_users.get_user("BLFD-01").owners is None


def test_register_user_with_owners_roundtrip():
    db_users.register_user("dept-test", "pw-12345", ["BLFD"], owners=["Quality"])
    db_users.register_user("all-test", "pw-12345", ["BLFD"])
    assert db_users.get_user("dept-test").owners == ["Quality"]
    assert db_users.get_user("all-test").owners is None


def test_init_db_migrates_pre_owners_database(tmp_path):
    """A users.db created before record-level permissions gains the column, unrestricted."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, "
                  "document_ids TEXT NOT NULL DEFAULT '[]')")
        c.execute("INSERT INTO users VALUES ('legacy', ?, '[\"BLFD\"]')", (db_users._hash("pw-12345"),))
    old = db_users._DB_PATH
    try:
        db_users.init_db(path)
        u = db_users.get_user("legacy")
        assert u.document_ids == ["BLFD"] and u.owners is None and u.check_password("pw-12345")
    finally:
        db_users._DB_PATH = old
