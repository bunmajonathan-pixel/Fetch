"""
test_company.py — the cross-source company page: signals built from financials,
customer behavior and supplier contracts, and the permission scope around them.
"""
import copy

import pytest

import server as srv
from retrieval import auth as _auth_mod
from retrieval import company

ALL3 = ("NL-2048", "password1234")
BLFD = ("BLFD-01", "blfd2024")
DEPT = ("BLFD-LOG", "blfdlog2024")


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _post(client, creds, **body):
    assert client.post("/api/signin", json={"id": creds[0], "password": creds[1]}).status_code == 200
    return client.post("/api/analyze/company", json=body)


# ---------------------------------------------------------------------------
# Signals on small hand-built inputs
# ---------------------------------------------------------------------------

def _fin(gm_bps=50, yoy=5.0):
    return {"latest": {"period": "Q2-2026", "revenue": 100.0, "gross_margin": 30.0, "operating_margin": 10.0,
                       "net_income": 5.0, "free_cash_flow": 6.0, "ttm_revenue": 400.0, "revenue_yoy": yoy},
            "margin_change_bps_yoy": {"gross_margin": gm_bps, "operating_margin": 10, "net_margin": 5}}


def _fin_flags(worse=(), improved=12, flat=3):
    f = _fin()
    f["scorecard"] = {"period": "Q2-2026", "prior_period": "Q2-2025", "improved": improved, "worsened": len(worse),
                      "flat": flat, "tracked": improved + len(worse) + flat,
                      "concerns": [{"label": w, "prior": 50.8, "value": 49.3} for w in worse]}
    return f


def _beh(worse=(), improved=10, flat=1, rev=8.0):
    return {"year": 2025, "customers": {"label": "Customers"}, "growth": {"revenue_pct": rev, "customers_pct": 5.0, "per_customer_pct": 2.9},
            "scorecard": {"improved": improved, "worsened": len(worse), "flat": flat, "tracked": improved + len(worse) + flat},
            "concerns": [{"label": w, "prior": 114.0, "value": 110.5, "unit": "%", "change": -3.4, "change_kind": "pts"} for w in worse],
            "rollforward": None}


def _con(unstarted=2, value=1500.0, at_risk=0, sole_k=100.0, total_k=1000.0, top3=30.0):
    return {"as_of": "2026-11-04", "risk_rule": {"days": 60, "text": "Active, expires within 60 days, renewal not started"},
            "kpis": {"contracts": 10, "value_k": total_k, "active_spend_k": total_k, "top3_share_pct": top3,
                     "at_risk": {"contracts": at_risk, "value_k": 500.0 if at_risk else 0.0},
                     "sole_source": {"contracts": 2, "value_k": sole_k}},
            "outlook": [{"label": "Q4 2026", "contracts": 3, "value_k": 2000.0,
                         "not_started": {"contracts": unstarted, "value_k": value}},
                        {"label": "Q1 2027", "contracts": 0, "value_k": 0.0, "not_started": {"contracts": 0, "value_k": 0.0}},
                        {"label": "Q2 2027", "contracts": 5, "value_k": 9999.0, "not_started": {"contracts": 5, "value_k": 9999.0}}],
            "renewal_pipeline": []}


def _ids(p):
    return {s["id"]: s for s in p["signals"]}


def test_margin_slipping_with_open_renewals_is_a_watch_item():
    s = _ids(company.profile("AAA", "A", _fin(gm_bps=-40), _beh(), _con(unstarted=2)))["margin_vs_renewals"]
    assert s["level"] == "watch" and s["sources"] == ["Financials", "Contracts"]
    assert "40 bp lower" in s["detail"] and "2 supplier contracts" in s["detail"]


def test_margin_holding_with_open_renewals_is_info_and_only_counts_the_next_two_quarters():
    s = _ids(company.profile("AAA", "A", _fin(gm_bps=50), _beh(), _con(unstarted=1, value=700.0)))["margin_vs_renewals"]
    assert s["level"] == "info" and "1 supplier contract " in s["detail"]       # the Q2 2027 row is outside the window
    assert "$700K" in s["detail"]


def test_margin_up_and_nothing_open_is_good():
    s = _ids(company.profile("AAA", "A", _fin(gm_bps=50), _beh(), _con(unstarted=0, value=0.0)))["margin_vs_renewals"]
    assert s["level"] == "good"


def test_lapsing_contracts_and_dependence_signals():
    p = company.profile("AAA", "A", _fin(), _beh(), _con(at_risk=1, sole_k=300.0, top3=45.0))
    ids = _ids(p)
    assert ids["lapsing"]["level"] == "watch" and "1 active contract," in ids["lapsing"]["detail"]
    assert "30.0% of active spend" in ids["supplier_dependence"]["detail"] and "45.0%" in ids["supplier_dependence"]["detail"]
    quiet = _ids(company.profile("AAA", "A", _fin(), _beh(), _con(at_risk=0, sole_k=100.0, top3=30.0)))
    assert "lapsing" not in quiet and "supplier_dependence" not in quiet


def test_customer_health_uses_both_sources():
    worse = _ids(company.profile("AAA", "A", _fin(), _beh(worse=("Net revenue retention (Q4)",)), _con()))["customer_health"]
    assert worse["level"] == "watch" and worse["sources"] == ["Customer behavior", "Financials"]
    assert worse["evidence"][-1] == {"label": "Net revenue retention (Q4)", "value": "114 → 110.5"}
    good = _ids(company.profile("AAA", "A", _fin(), _beh(), _con()))["customer_health"]
    assert good["level"] == "good"


def test_growth_trend_needs_a_two_point_gap():
    assert "growth_trend" not in _ids(company.profile("AAA", "A", _fin(yoy=7.0), _beh(rev=8.0), _con()))
    slow = _ids(company.profile("AAA", "A", _fin(yoy=5.0), _beh(rev=8.0), _con()))["growth_trend"]
    assert slow["level"] == "watch" and "slowed" in slow["title"]
    fast = _ids(company.profile("AAA", "A", _fin(yoy=11.0), _beh(rev=8.0), _con()))["growth_trend"]
    assert fast["level"] == "good" and "sped up" in fast["title"]


def test_partial_view_skips_signals_that_compare_totals_with_the_company():
    full = company.profile("AAA", "A", _fin(), _beh(), _con(sole_k=500.0, top3=60.0, total_k=2000.0))
    part = company.profile("AAA", "A", _fin(), _beh(), _con(sole_k=500.0, top3=60.0, total_k=2000.0), partial=True)
    assert "supplier_dependence" in _ids(full) and full["weight"]["pct"] == 0.5
    assert "supplier_dependence" not in _ids(part) and part["weight"] is None and part["partial_contracts"]


def test_signals_are_sorted_watch_first_and_counted():
    p = company.profile("AAA", "A", _fin(gm_bps=-40), _beh(), _con(at_risk=1))
    levels = [s["level"] for s in p["signals"]]
    assert levels == sorted(levels, key={"watch": 0, "info": 1, "good": 2}.get)
    assert sum(p["counts"].values()) == len(p["signals"])


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------

def test_real_data_profile_for_each_company(client):
    d = _post(client, ALL3).get_json()
    by = {c["ticker"]: c for c in d["companies"]}
    assert set(by) == {"BLFD", "HDYN", "MCLD"}
    assert by["MCLD"]["weight"]["pct"] == 20.9 and by["BLFD"]["weight"]["pct"] == 2.8
    assert "customer_health" in {s["id"]: s for s in by["MCLD"]["signals"]}
    assert any(c["label"] == "Net revenue retention (Q4)" for c in by["MCLD"]["behavior"]["concerns"])


# ---------------------------------------------------------------------------
# API scope
# ---------------------------------------------------------------------------

def test_api_requires_sign_in(client):
    assert client.post("/api/analyze/company", json={}).status_code == 401


def test_single_company_user_sees_only_their_company(client):
    r = _post(client, BLFD)
    d = r.get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD"]
    body = r.get_data(as_text=True)
    assert "HDYN" not in body and "MCLD" not in body and "Halcyon" not in body and "Meridian" not in body


def test_foreign_ticker_is_forbidden_and_audited(client):
    r = _post(client, BLFD, ticker="MCLD")
    assert r.status_code == 403
    assert any(e.get("event") == "analyze_denied" and e.get("detail", {}).get("kind") == "company"
               for e in srv._audit_recent(50))


def test_ticker_filter_returns_one_company(client):
    d = _post(client, ALL3, ticker="hdyn").get_json()
    assert [c["ticker"] for c in d["companies"]] == ["HDYN"]


def test_department_limited_user_gets_partial_contracts(client):
    full = _post(client, BLFD).get_json()["companies"][0]
    part = _post(client, DEPT).get_json()["companies"][0]
    assert part["partial_contracts"] and not full["partial_contracts"]
    assert part["contracts"]["kpis"]["contracts"] < full["contracts"]["kpis"]["contracts"]
    assert part["weight"] is None
    # financials and behavior are company-wide
    assert part["financials"] == full["financials"] and part["behavior"] == full["behavior"]


def test_company_view_is_audited(client):
    _post(client, BLFD)
    ev = [e for e in srv._audit_recent(20) if e.get("event") == "analyze" and e.get("detail", {}).get("kind") == "company"]
    assert ev and ev[-1]["detail"]["companies"] == ["BLFD"]


def test_financial_flags_raise_a_watch_item_naming_the_measures():
    sigs = {s["id"]: s for s in company.signals(_fin_flags(["Free cash flow", "Cash balance"]), _beh(), _con(), partial=False)}
    s = sigs["financial_flags"]
    assert s["level"] == "watch" and "Free cash flow" in s["detail"] and "Cash balance" in s["detail"]
    assert s["sources"] == ["Financials"] and "2 measures" in s["detail"]


def test_no_worse_financial_measure_is_a_good_signal_and_flat_heavy_is_silent():
    good = {s["id"]: s for s in company.signals(_fin_flags(), _beh(), _con(), partial=False)}
    assert good["financial_flags"]["level"] == "good"
    quiet = {s["id"] for s in company.signals(_fin_flags(improved=2, flat=10), _beh(), _con(), partial=False)}
    assert "financial_flags" not in quiet
    assert "financial_flags" not in {s["id"] for s in company.signals(_fin(), _beh(), _con(), partial=False)}   # no scorecard
