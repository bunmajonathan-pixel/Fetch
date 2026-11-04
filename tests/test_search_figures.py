"""
test_search_figures.py — key numbers shown under a search hit that is a quarterly or
customer-behavior report, and the permission scope around them.
"""
import pytest

import server as srv
from retrieval import auth as _auth_mod
from retrieval import financials

from test_company import ALL3, BLFD


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _search(client, creds, query):
    assert client.post("/api/signin", json={"id": creds[0], "password": creds[1]}).status_code == 200
    return client.post("/api/retrieve", json={"query": query})


def test_report_figures_match_the_quarter_and_its_flags():
    f = financials.report_figures("BLFD", "Q3-2026")
    assert f["kind"] == "quarter" and f["revenue"] == 1230.4 and f["eps"] == 0.26
    assert f["worsened"] >= 5 and f["worsened"] > f["improved"] and f["concerns"]
    assert f["improved"] + f["worsened"] + f["flat"] > 10


def test_first_quarters_have_figures_but_no_flags_and_unknown_ones_none():
    f = financials.report_figures("BLFD", "Q3-2023")           # no year-earlier quarter to compare with
    assert f["revenue"] and f["improved"] is None and f["concerns"] == []
    assert financials.report_figures("BLFD", "Q1-2031") is None
    assert financials.report_figures("ZZZZ", "Q3-2026") is None


def test_quarterly_hits_carry_figures_and_other_hits_do_not(client):
    rows = _search(client, ALL3, "Q3 2026 revenue").get_json()["rows"]
    quarterly = [r for r in rows if r["file"].endswith("_Quarterly_Report.pdf")]
    assert {r["ticker"] for r in quarterly} == {"BLFD", "HDYN", "MCLD"}
    assert all(r["figures"]["kind"] == "quarter" and r["figures"]["period"] == "Q3-2026" for r in quarterly)
    contracts = [r for r in rows if r["category"] == "Supplier Contracts"]
    assert contracts and all(r["figures"] is None for r in contracts)


def test_behavior_hits_carry_the_year_scorecard(client):
    rows = _search(client, ALL3, "customer behavior 2025").get_json()["rows"]
    beh = {r["ticker"]: r["figures"] for r in rows if r["category"] == "behavior" and r["period"] == "2025"}
    assert beh["MCLD"]["kind"] == "behavior" and beh["MCLD"]["worsened"] == 1
    assert beh["MCLD"]["concerns"] == ["Net revenue retention (Q4)"]


def test_figures_never_cross_the_users_company_scope(client):
    r = _search(client, BLFD, "Q3 2026 revenue")
    body = r.get_data(as_text=True)
    assert {x["ticker"] for x in r.get_json()["rows"]} == {"BLFD"}
    assert "HDYN" not in body and "MCLD" not in body and "Halcyon" not in body and "Meridian" not in body
