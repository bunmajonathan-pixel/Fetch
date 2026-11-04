"""
test_alerts.py — the cross-company "needs attention" list: surprises, ordering,
and the same permission scope as the company page.
"""
import pytest

import server as srv
from retrieval import alerts, company, financials
from retrieval import auth as _auth_mod

from test_company import _beh, _con, _fin, ALL3, BLFD, DEPT


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
    return client.post("/api/analyze/alerts", json=body)


def _profile(ticker="AAA", surprises=(), **kw):
    f = _fin(**kw)
    f["surprises"] = list(surprises)
    return company.profile(ticker, ticker + " Co", f, _beh(), _con(), partial=False)


def _s(label="Net income", direction="below", pct=-12.0):
    return {"key": "net_income", "label": label, "unit": "$M", "period": "Q3-2026", "actual": 76.5, "expected": 107.8,
            "low": 99.3, "high": 116.3, "miss_pct": pct, "direction": direction}


# ---------------------------------------------------------------------------
# The estimate method used for surprises
# ---------------------------------------------------------------------------

def test_a_result_inside_the_range_is_not_a_surprise_and_a_big_miss_is():
    base = [100 + 10 * i + (4 if i % 2 else -4) for i in range(12)]
    ok = base + [base[8] + (base[11] - base[7] + base[10] - base[6] + base[9] - base[5]) / 3]
    rows = lambda revs: [{"period": f"P{i}", "revenue": r, "operating_income": r * .2, "net_income": r * .1,
                          "eps": r / 1000, "free_cash_flow": r * .05} for i, r in enumerate(revs)]
    assert financials._surprises(rows(ok)) == []
    bad = financials._surprises(rows(base + [ok[-1] * 0.7]))
    assert bad and bad[0]["direction"] == "below" and bad[0]["miss_pct"] < -20
    assert financials._surprises(rows(base[:9])) == []            # not enough history to judge


# ---------------------------------------------------------------------------
# Building the list
# ---------------------------------------------------------------------------

def test_a_miss_makes_a_watch_alert_and_a_beat_a_good_one():
    miss = alerts.build([_profile(surprises=[_s()])])["alerts"]
    s = next(a for a in miss if a["kind"] == "surprise")
    assert s["level"] == "watch" and s["screen"] == "financials" and "below" in s["title"]
    assert "Net income" in s["detail"] and s["evidence"][0]["label"] == "Net income, Q3-2026"
    beat = next(a for a in alerts.build([_profile(surprises=[_s(direction="above", pct=9.0)])])["alerts"] if a["kind"] == "surprise")
    assert beat["level"] == "good" and "above" in beat["title"]


def test_alerts_are_ranked_watch_first_surprises_first_and_counted():
    out = alerts.build([_profile("BBB", surprises=[_s()], gm_bps=-40), _profile("AAA", gm_bps=50)])
    levels = [a["level"] for a in out["alerts"]]
    assert levels == sorted(levels, key={"watch": 0, "info": 1, "good": 2}.get)
    assert out["alerts"][0]["kind"] == "surprise" and out["alerts"][0]["ticker"] == "BBB"
    assert sum(out["counts"].values()) == len(out["alerts"])
    assert {c["ticker"] for c in out["companies"]} == {"AAA", "BBB"}
    assert len({a["id"] for a in out["alerts"]}) == len(out["alerts"])


def test_no_surprises_means_only_signals():
    assert all(a["kind"] == "signal" for a in alerts.build([_profile()])["alerts"])


# ---------------------------------------------------------------------------
# API scope
# ---------------------------------------------------------------------------

def test_api_requires_sign_in(client):
    assert client.post("/api/analyze/alerts", json={}).status_code == 401


def test_real_data_feed_covers_every_company_and_flags_the_weak_quarter(client):
    d = _post(client, ALL3).get_json()
    assert {c["ticker"] for c in d["companies"]} == {"BLFD", "HDYN", "MCLD"}
    surprises = {a["ticker"]: a for a in d["alerts"] if a["kind"] == "surprise"}
    assert surprises["BLFD"]["level"] == "watch" and surprises["HDYN"]["level"] == "watch"
    assert surprises["MCLD"]["level"] == "good"
    assert d["alerts"][0]["level"] == "watch" and d["counts"]["watch"] >= 3


def test_single_company_user_sees_only_their_company(client):
    r = _post(client, BLFD)
    body = r.get_data(as_text=True)
    assert {a["ticker"] for a in r.get_json()["alerts"]} == {"BLFD"}
    assert "HDYN" not in body and "MCLD" not in body and "Halcyon" not in body and "Meridian" not in body


def test_foreign_ticker_is_forbidden_and_audited(client):
    assert _post(client, BLFD, ticker="MCLD").status_code == 403
    assert any(e.get("event") == "analyze_denied" and e.get("detail", {}).get("kind") == "alerts"
               for e in srv._audit_recent(50))


def test_ticker_filter_and_audit(client):
    d = _post(client, ALL3, ticker="hdyn").get_json()
    assert {a["ticker"] for a in d["alerts"]} == {"HDYN"}
    ev = [e for e in srv._audit_recent(20) if e.get("event") == "analyze" and e.get("detail", {}).get("kind") == "alerts"]
    assert ev and ev[-1]["detail"]["companies"] == ["HDYN"]


def test_department_limited_user_gets_alerts_from_their_contracts_only(client):
    full = _post(client, BLFD).get_json()
    part = _post(client, DEPT).get_json()
    assert part["scope"]["owners"] and full["scope"]["owners"] is None
    assert not any(a["id"].endswith(":supplier_dependence") for a in part["alerts"])      # needs the whole register
    assert any(a["kind"] == "surprise" for a in part["alerts"])                          # financials are company-wide
