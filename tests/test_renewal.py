"""
test_renewal.py — renewal pipeline, at-risk flags, sole-source and supplier
concentration, plus the outlook across the next quarters.
"""
import csv
import sqlite3
from datetime import date

import pytest

import retrieval.auth as _auth_mod
import server as srv
from retrieval import contracts
from retrieval.contracts import Filters

AAA = {"AAA"}
Q4 = Filters(expiry_from=date(2026, 10, 1), expiry_to=date(2026, 12, 31))

ROWS = [
    # id, ticker, supplier, category, owner, start, expiry, status, value, renewal, sole
    ("R1", "AAA", "Alpha",   "Cat1", "Ops", "2024-01-01", "2026-11-14", "Active",  1000, "Not started",    "Yes"),   # +10d  at risk
    ("R2", "AAA", "Alpha",   "Cat1", "Ops", "2024-01-01", "2026-12-04", "Active",  3000, "In negotiation", "No"),    # +30d
    ("R3", "AAA", "Beta",    "Cat2", "Fin", "2024-01-01", "2026-12-31", "Active",  2000, "Not started",    "No"),    # +57d  at risk
    ("R4", "AAA", "Gamma",   "Cat2", "Fin", "2024-01-01", "2026-10-15", "Expired",  500, "",               "No"),    # past
    ("R5", "AAA", "Delta",   "Cat1", "Ops", "2024-01-01", "2027-02-15", "Active",  4000, "Not started",    "No"),    # +103d not at risk yet
    ("R6", "AAA", "Epsilon", "Cat2", "Fin", "2024-01-01", "2027-01-20", "Active",  1000, "Renewal agreed", "Yes"),   # +77d
    ("R7", "BBB", "Omega",   "Cat1", "Ops", "2024-01-01", "2026-12-01", "Active",  9999, "Not started",    "No"),    # other company
]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "c.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(contracts._ALL_COLUMNS))
        w.writerows(ROWS)
    old = contracts._DB_PATH
    contracts.init_contracts(tmp_path / "c.db", path)
    yield tmp_path
    contracts._DB_PATH = old


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _api(client, uid, pw, **body):
    assert client.post("/api/signin", json={"id": uid, "password": pw}).status_code == 200
    return client.post("/api/analyze/contracts", json=body)


# ---------------------------------------------------------------------------
# At-risk, sole-source, pipeline (exact)
# ---------------------------------------------------------------------------

def test_at_risk_is_active_unstarted_and_within_60_days(db):
    k = contracts.analyze(AAA, None, Q4)["kpis"]
    assert k["contracts"] == 3 and k["value_k"] == 6000
    assert k["at_risk"] == {"contracts": 2, "value_k": 3000}           # R1 (+10d) and R3 (+57d)
    assert k["sole_source"] == {"contracts": 1, "value_k": 1000}       # R1


def test_unstarted_contract_beyond_60_days_is_not_at_risk(db):
    a = contracts.analyze(AAA, None, Filters(expiry_from=date(2027, 1, 1), expiry_to=date(2027, 3, 31)))
    assert a["kpis"]["contracts"] == 2 and a["kpis"]["at_risk"] == {"contracts": 0, "value_k": 0}   # R5 is +103d


def test_renewal_pipeline_counts_and_shares(db):
    p = {r["name"]: r for r in contracts.analyze(AAA, None, Q4)["renewal_pipeline"]}
    assert [n for n in p] == list(contracts.RENEWAL_STATES)
    assert p["Not started"]["contracts"] == 2 and p["Not started"]["value_k"] == 3000
    assert p["In negotiation"]["value_k"] == 3000 and p["Not started"]["share_pct"] == 50.0
    assert p["Renewal agreed"]["contracts"] == 0 and p["Renewal agreed"]["share_pct"] == 0.0


def test_pipeline_is_empty_when_not_looking_at_active_contracts(db):
    a = contracts.analyze(AAA, None, Filters(status="Expired"))
    assert a["kpis"]["contracts"] == 1 and all(r["contracts"] == 0 for r in a["renewal_pipeline"])


def test_renewal_and_sole_source_filters(db):
    ids = lambda f: [r["contract_id"] for r in contracts.fetch_contracts(AAA, None, f)]
    assert ids(Filters(renewal="Not started")) == ["R1", "R3", "R5"]
    assert ids(Filters(sole_source=True)) == ["R1", "R6"]
    assert ids(Filters(renewal="Not started", sole_source=True)) == ["R1"]


def test_share_of_active_spend_ignores_renewal_filter(db):
    """Narrowing by renewal state must not shrink the denominator of the exposure percentage."""
    a = contracts.analyze(AAA, None, Filters(renewal="Not started", expiry_from=date(2026, 10, 1),
                                             expiry_to=date(2026, 12, 31)))
    assert a["kpis"]["active_spend_k"] == 11000                         # R1+R2+R3+R5+R6, all of AAA
    assert a["kpis"]["value_k"] == 3000 and a["kpis"]["exposure_pct"] == round(100 * 3000 / 11000, 1)


# ---------------------------------------------------------------------------
# Concentration and outlook
# ---------------------------------------------------------------------------

def test_concentration_hhi_and_flags(db):
    c = {x["category"]: x for x in contracts.analyze(AAA, None, Q4)["concentration"]}
    assert c["Cat1"]["hhi"] == 5000 and c["Cat1"]["level"] == "High"          # Alpha 4000 / Delta 4000
    assert c["Cat1"]["top_supplier"] == "Alpha" and c["Cat1"]["top_share_pct"] == 50.0 and c["Cat1"]["top_flagged"]
    assert c["Cat2"]["hhi"] == round((2000 / 3000 * 100) ** 2 + (1000 / 3000 * 100) ** 2)   # 5556
    assert c["Cat2"]["sole_source"] == {"contracts": 1, "value_k": 1000}        # R6
    # computed over every Active contract, not just those expiring in the window
    assert c["Cat1"]["value_k"] == 8000


@pytest.mark.parametrize("n,level", [(10, "Low"), (5, "Moderate"), (2, "High")])
def test_concentration_bands(n, level):
    rows = [{"category": "C", "supplier": f"S{i}", "annual_value_k": 100, "sole_source": "No"} for i in range(n)]
    c = contracts._concentration(rows)[0]
    assert c["hhi"] == round(10000 / n) and c["level"] == level


def test_outlook_shows_the_next_four_quarters(db):
    o = contracts.analyze(AAA, None, Q4)["outlook"]
    assert [q["label"] for q in o] == ["Q4 2026", "Q1 2027", "Q2 2027", "Q3 2027"]
    assert (o[0]["contracts"], o[0]["value_k"], o[0]["not_started"]["value_k"]) == (3, 6000, 3000)
    assert (o[1]["contracts"], o[1]["value_k"], o[1]["not_started"]["value_k"]) == (2, 5000, 4000)
    assert o[2]["contracts"] == 0 and o[3]["value_k"] == 0


def test_department_scope_limits_every_new_statistic(db):
    a = contracts.analyze(AAA, {"Fin"}, Q4)
    assert a["kpis"]["at_risk"] == {"contracts": 1, "value_k": 2000}          # only R3
    assert [c["category"] for c in a["concentration"]] == ["Cat2"]
    assert a["outlook"][0]["value_k"] == 2000 and a["outlook"][1]["value_k"] == 1000
    assert "Alpha" not in str(a) and "Delta" not in str(a) and "Cat1" not in str(a["concentration"])
    assert contracts.analyze(AAA, set(), Q4)["kpis"]["contracts"] == 0


# ---------------------------------------------------------------------------
# Parsing, storage, migration
# ---------------------------------------------------------------------------

def test_parse_filters():
    f = contracts.parse_filters({"renewal": "not started", "sole_source": "yes"})
    assert (f.renewal, f.sole_source) == ("Not started", True)
    assert contracts.parse_filters({"renewal": "all"}).renewal is None
    assert contracts.parse_filters({}).sole_source is False
    for bad in ({"renewal": "maybe"}, {"sole_source": 5}):
        with pytest.raises(ValueError):
            contracts.parse_filters(bad)


def test_old_csv_without_renewal_columns_still_loads(tmp_path):
    path = tmp_path / "old.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(contracts._COLUMNS)
        w.writerow(("O-1", "AAA", "Alpha", "Cat1", "Ops", "2024-01-01", "2026-12-04", "Active", 100))
    old = contracts._DB_PATH
    try:
        contracts.init_contracts(tmp_path / "o.db", path)
        r = contracts.fetch_contracts(AAA, None)[0]
        assert r["renewal_status"] == "" and r["sole_source"] == "No"
    finally:
        contracts._DB_PATH = old


def test_database_from_before_the_new_columns_is_rebuilt(tmp_path):
    dbp = tmp_path / "legacy.db"
    with sqlite3.connect(dbp) as c:
        c.executescript("""CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, ticker TEXT, supplier TEXT,
            category TEXT, owner TEXT, start_date TEXT, expiry_date TEXT, status TEXT, annual_value_k INTEGER);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO contracts VALUES ('X','AAA','S','C','O','2024-01-01','2026-12-01','Active',1);
            INSERT INTO meta VALUES ('csv_sha256','stale');""")
    path = tmp_path / "c.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(contracts._ALL_COLUMNS))
        w.writerows(ROWS)
    old = contracts._DB_PATH
    try:
        assert contracts.init_contracts(dbp, path) == len(ROWS)
        assert contracts.fetch_contracts(AAA, None, Filters(renewal="Not started"))
    finally:
        contracts._DB_PATH = old


def test_real_dataset_fields():
    with open(srv.CONTRACTS_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        assert r["sole_source"] in ("Yes", "No")
        assert (r["renewal_status"] in contracts.RENEWAL_STATES) == (r["status"] == "Active"), r
    assert 0.08 < sum(r["sole_source"] == "Yes" for r in rows) / len(rows) < 0.25


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_blfd_q4_2026_demo_numbers(client):
    d = _api(client, "BLFD-01", "blfd2024", quarter="Q4-2026").get_json()
    assert d["kpis"]["contracts"] == 5
    assert d["kpis"]["at_risk"] == {"contracts": 1, "value_k": 3800}           # BLFD-SC-2411, Dec 15, not started
    assert [q["label"] for q in d["outlook"]][0] == "Q4 2026" and d["outlook"][0]["value_k"] == 10770
    assert d["concentration"] and d["risk_rule"]["days"] == 60


def test_api_department_user_sees_only_their_concentration_and_pipeline(client):
    d = _api(client, "BLFD-LOG", "blfdlog2024", quarter="Q4-2026").get_json()
    assert {c["category"] for c in d["concentration"]} <= {"Logistics"}
    rows_ok = all(r["owner"] == "Logistics" for r in d["rows"])
    assert rows_ok and sum(p["contracts"] for p in d["renewal_pipeline"]) == d["kpis"]["contracts"] == 2


def test_api_filters_and_validation(client):
    d = _api(client, "BLFD-01", "blfd2024", quarter="Q4-2026", renewal="Not started").get_json()
    assert d["filters"]["renewal"] == "Not started"
    assert all(r["renewal_status"] == "Not started" for r in d["rows"])
    assert client.post("/api/analyze/contracts", json={"renewal": "maybe"}).status_code == 400
    assert client.post("/api/analyze/contracts", json={"sole_source": "x" * 5, "renewal": ["a"]}).status_code == 400
