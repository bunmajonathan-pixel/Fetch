"""
test_behavior.py — customer-behavior analytics: PDF extraction, year-over-year
scoring, permission scope, and the API.
"""
import copy
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

import retrieval.auth as _auth_mod
import server as srv
from retrieval import behavior
from retrieval.behavior import Filters

ROOT = Path(__file__).resolve().parent.parent
BEH = srv.DATA_ROOT / "behavior"

_spec = importlib.util.spec_from_file_location("extract_behavior", ROOT / "tools" / "extract_behavior.py")
eb = importlib.util.module_from_spec(_spec)
sys.modules["extract_behavior"] = eb
_spec.loader.exec_module(eb)


@pytest.fixture(scope="module")
def reports():
    return eb.load_all()


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _post(client, uid, pw, **body):
    assert client.post("/api/signin", json={"id": uid, "password": pw}).status_code == 200
    return client.post("/api/analyze/behavior", json=body)


BLFD = ("BLFD-01", "blfd2024")
ALL3 = ("NL-2048", "password1234")
DEPT = ("BLFD-LOG", "blfdlog2024")


# ---------------------------------------------------------------------------
# Extraction from the PDFs
# ---------------------------------------------------------------------------

def test_every_report_reconciles(reports):
    assert len(reports) == 6
    assert eb.reconcile(reports) == []


def test_known_values_from_the_pdfs(reports):
    def v(t, y, key, section=None):
        return next(r for r in reports[(t, y)]["rows"] if r["key"] == key and (section is None or r["section"] == section))
    assert v("BLFD", 2025, "buying_households_millions")["value"] == 42.5
    assert v("BLFD", 2025, "buying_households_millions")["printed_change"] == 3.2
    assert v("HDYN", 2024, "active_customer_accounts_year_end")["value"] == 3420
    assert v("MCLD", 2025, "annual_recurring_revenue_year_end_m")["unit"] == "$M"
    assert v("MCLD", 2025, "net_revenue_retention_q4")["printed_change"] == -3.4
    assert v("HDYN", 2025, "customers_lost")["change_kind"] == "pct"
    assert v("HDYN", 2025, "logo_churn_rate")["change_kind"] == "pts"
    # the same label in two different tables stays two different metrics
    assert v("MCLD", 2025, "enterprise", "Customers by plan")["value"] == 21.0
    assert v("MCLD", 2025, "enterprise", "Revenue by customer segment")["value"] == 55.0
    assert reports[("MCLD", 2025)]["revenue"] == 1456.1 and reports[("MCLD", 2025)]["revenue_change"] == 16.8


@pytest.mark.parametrize("tamper, expect", [
    (lambda r: r[("BLFD", 2025)]["rows"][0].update(value=99.0), "prints"),                       # change no longer matches
    (lambda r: r[("HDYN", 2025)].update(revenue=2000.0), "sum of quarters"),                     # revenue vs. financials
    (lambda r: next(x for x in r[("MCLD", 2024)]["rows"] if x["section"] == "Customers by plan").update(value=50.0),
     "100%"),                                                                                     # mix no longer adds up
    (lambda r: next(x for x in r[("HDYN", 2025)]["rows"] if x["key"] == "customers_lost").update(value=300.0),
     "year-end customers"),                                                                       # roll-forward breaks
    (lambda r: next(x for x in r[("MCLD", 2025)]["rows"] if x["key"] == "stickiness_dau_mau").update(value=50.0),
     "stickiness"),
])
def test_reconcile_detects_tampering(reports, tamper, expect):
    bad = copy.deepcopy(reports)
    tamper(bad)
    problems = eb.reconcile(bad)
    assert problems and any(expect in p for p in problems), problems


def test_committed_csv_matches_the_pdfs(reports, tmp_path, monkeypatch):
    """The CSV in git must be exactly what the extractor produces from today's PDFs."""
    monkeypatch.setattr(eb, "OUT", tmp_path / "b.csv")
    assert eb.main([]) == 0
    assert (tmp_path / "b.csv").read_text() == (BEH / "behavior.csv").read_text()


def test_direction_and_roles_in_csv():
    rows = list(csv.DictReader((BEH / "behavior.csv").open()))
    by = {(r["ticker"], r["key"]): r for r in rows if r["year"] == "2025"}
    assert by[("HDYN", "customers_lost")]["direction"] == "down"
    assert by[("HDYN", "net_promoter_score")]["direction"] == "up"
    assert by[("BLFD", "spend_per_purchase_occasion")]["direction"] == "neutral"
    assert {r["direction"] for r in rows if r["kind"] == "mix"} == {"neutral"}
    for tk in ("BLFD", "HDYN", "MCLD"):
        assert sum(1 for r in rows if r["ticker"] == tk and r["year"] == "2025" and r["role"] == "volume") == 1


# ---------------------------------------------------------------------------
# Scoring (exact arithmetic on a tiny dataset)
# ---------------------------------------------------------------------------

FIELDS = ["ticker", "year", "section", "kind", "key", "label", "value", "unit",
          "printed_change", "change_kind", "direction", "role"]


def _row(t, y, key, value, unit="", chg="", kind_="", direction="up", role="", section="S", kind="metric"):
    return dict(ticker=t, year=y, section=section, kind=kind, key=key, label=key.replace("_", " ").title(),
                value=value, unit=unit, printed_change=chg, change_kind=kind_, direction=direction, role=role)


@pytest.fixture()
def tiny(tmp_path):
    saved = behavior._PATH

    def make(rows):
        p = tmp_path / "b.csv"
        with p.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        behavior.init_behavior(p)

    yield make
    behavior.init_behavior(saved)


def _base(t="AAA"):
    return [
        _row(t, 2024, "revenue", 1000.0, "$M", role="revenue", section="Overview"),
        _row(t, 2025, "revenue", 1100.0, "$M", 10.0, "pct", role="revenue", section="Overview"),
        _row(t, 2024, "customers", 200.0, role="volume"), _row(t, 2025, "customers", 220.0, "", 10.0, "pct", role="volume"),
    ]


def test_assessment_thresholds_and_direction(tiny):
    rows = _base() + [
        _row("AAA", 2024, "rate_flat", 50.0, "%"), _row("AAA", 2025, "rate_flat", 50.9, "%", 0.9, "pts"),
        _row("AAA", 2024, "rate_up", 50.0, "%"), _row("AAA", 2025, "rate_up", 51.0, "%", 1.0, "pts"),
        _row("AAA", 2024, "count_flat", 100.0), _row("AAA", 2025, "count_flat", 101.9, "", 1.9, "pct"),
        _row("AAA", 2024, "count_up", 100.0), _row("AAA", 2025, "count_up", 102.0, "", 2.0, "pct"),
        _row("AAA", 2024, "churn", 10.0, "%", direction="down"), _row("AAA", 2025, "churn", 12.0, "%", 2.0, "pts", "down"),
        _row("AAA", 2024, "tickets", 50.0, direction="down"), _row("AAA", 2025, "tickets", 40.0, "", -20.0, "pct", "down"),
        _row("AAA", 2024, "size", 100.0, direction="neutral"), _row("AAA", 2025, "size", 150.0, "", 50.0, "pct", "neutral"),
    ]
    tiny(rows)
    c = behavior.analyze({"AAA"}, Filters())["companies"][0]
    a = {r["key"]: r["assessment"] for s in c["sections"] for r in s["rows"]}
    assert a == {"customers": "improved", "rate_flat": "flat", "rate_up": "improved", "count_flat": "flat",
                 "count_up": "improved", "churn": "worsened", "tickets": "improved", "size": None}
    assert c["scorecard"] == {"improved": 4, "worsened": 1, "flat": 2, "tracked": 7}      # revenue is not scored
    assert [r["key"] for r in c["concerns"]] == ["churn"]
    assert c["improved"][0]["key"] == "tickets"                                           # biggest relative move first


def test_growth_split_is_exact(tiny):
    tiny(_base())
    c = behavior.analyze({"AAA"}, Filters())["companies"][0]
    assert c["growth"] == {"revenue_pct": 10.0, "customers_pct": 10.0, "per_customer_pct": 0.0}
    rows = _base()
    rows[1]["value"] = 1155.0                     # revenue +15.5% on +10% customers
    tiny(rows)
    g = behavior.analyze({"AAA"}, Filters())["companies"][0]["growth"]
    assert g["per_customer_pct"] == 5.0           # 1.155 / 1.10 - 1


def test_first_year_has_nothing_to_compare(tiny):
    tiny(_base())
    c = behavior.analyze({"AAA"}, Filters(year=2024))["companies"][0]
    assert c["prior_year"] is None and c["growth"] is None and c["scorecard"] is None
    assert c["improved"] == [] and c["concerns"] == []


def test_rollforward_and_churn(tiny):
    rows = _base() + [_row("AAA", 2025, "won", 40.0, role="gain"), _row("AAA", 2025, "lost", 20.0, role="loss", direction="down")]
    tiny(rows)
    roll = behavior.analyze({"AAA"}, Filters())["companies"][0]["rollforward"]
    assert (roll["start"], roll["gained"], roll["lost"], roll["end"]) == (200.0, 40.0, 20.0, 220.0)
    assert roll["churn_pct"] == 10.0


def test_assessment_filter_narrows_tables_but_not_the_scorecard(tiny):
    rows = _base() + [_row("AAA", 2024, "a", 100.0), _row("AAA", 2025, "a", 110.0, "", 10.0, "pct"),
                      _row("AAA", 2024, "b", 100.0, direction="down"), _row("AAA", 2025, "b", 110.0, "", 10.0, "pct", "down")]
    tiny(rows)
    full = behavior.analyze({"AAA"}, Filters())["companies"][0]
    only = behavior.analyze({"AAA"}, Filters(assessment="worsened"))["companies"][0]
    assert [r["key"] for s in only["sections"] for r in s["rows"]] == ["b"]
    assert only["scorecard"] == full["scorecard"]


def test_comparison_needs_two_companies_and_skips_unreported_measures(tiny):
    rows = _base("AAA") + _base("BBB")
    rows += [_row("AAA", 2024, "logo_churn_rate", 9.0, "%", direction="down"),
             _row("AAA", 2025, "logo_churn_rate", 8.0, "%", -1.0, "pts", "down")]
    tiny(rows)
    assert behavior.analyze({"AAA"}, Filters())["comparison"] == []
    comp = {m["measure"]: m for m in behavior.analyze({"AAA", "BBB"}, Filters())["comparison"]}
    assert comp["Logo churn rate"]["values"] == {"AAA": 8.0, "BBB": None} and comp["Logo churn rate"]["best"] is None
    assert "Net Promoter Score" not in comp
    assert comp["Revenue growth, 2025 vs. 2024"]["values"] == {"AAA": 10.0, "BBB": 10.0}


def test_out_of_scope_company_never_appears(tiny):
    tiny(_base("AAA") + _base("BBB"))
    d = behavior.analyze({"AAA"}, Filters())
    assert [c["ticker"] for c in d["companies"]] == ["AAA"] and d["comparison"] == []
    assert "BBB" not in str(d)
    assert behavior.scope_years({"AAA"}) == [2024, 2025]
    assert behavior.analyze(set(), Filters())["companies"] == []


def test_parse_filters():
    f = behavior.parse_filters({"ticker": " blfd ", "year": "2024", "assessment": "Worsened"})
    assert (f.ticker, f.year, f.assessment) == ("BLFD", 2024, "worsened")
    assert behavior.parse_filters({}).year is None and behavior.parse_filters({"year": "latest"}).year is None
    for bad in ({"year": "soon"}, {"year": 1999}, {"assessment": "great"}):
        with pytest.raises(ValueError):
            behavior.parse_filters(bad)


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------

def test_real_data_matches_the_reports():
    d = behavior.analyze({"BLFD", "HDYN", "MCLD"}, Filters())
    c = {x["ticker"]: x for x in d["companies"]}
    assert d["year"] == 2025 and d["years"] == [2024, 2025]
    assert c["BLFD"]["revenue"]["growth_pct"] == 4.6 and c["BLFD"]["customers"]["growth_pct"] == 3.2
    assert c["HDYN"]["rollforward"] == {"start": 3420.0, "gained": 455.0, "lost": 265.0, "end": 3610.0,
                                        "gained_label": "New customers won", "lost_label": "Customers lost",
                                        "churn_pct": 7.7}
    assert c["MCLD"]["rollforward"]["churn_pct"] == 7.2
    assert [r["label"] for r in c["MCLD"]["concerns"]] == ["Net revenue retention (Q4)"]
    assert c["BLFD"]["concerns"] == [] and c["BLFD"]["rollforward"] is None
    # growth of revenue = customers x revenue per customer, within rounding of the printed percentages
    for x in c.values():
        g = x["growth"]
        assert (1 + g["customers_pct"] / 100) * (1 + g["per_customer_pct"] / 100) == pytest.approx(
            1 + g["revenue_pct"] / 100, abs=0.002)
    comp = {m["measure"]: m for m in d["comparison"]}
    assert comp["Net Promoter Score"]["values"] == {"BLFD": 39.0, "HDYN": 45.0, "MCLD": 44.0}
    assert comp["Logo churn rate"]["values"]["BLFD"] is None and comp["Logo churn rate"]["best"] == "MCLD"


def test_every_printed_change_is_used_for_the_assessment():
    d = behavior.analyze({"BLFD", "HDYN", "MCLD"}, Filters())
    for c in d["companies"]:
        for s in c["sections"]:
            for r in s["rows"]:
                assert r["change"] is not None and r["prior"] is not None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_requires_sign_in(client):
    assert client.post("/api/analyze/behavior", json={}).status_code == 401


def test_api_single_company_user_sees_only_their_company(client):
    r = _post(client, *BLFD)
    assert r.status_code == 200
    d = r.get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD"] and d["comparison"] == []
    assert [c["ticker"] for c in d["scope"]["companies"]] == ["BLFD"] and d["scope"]["years"] == [2024, 2025]
    body = r.get_data(as_text=True)
    assert "HDYN" not in body and "MCLD" not in body and "Halcyon" not in body and "Meridian" not in body


def test_api_multi_company_user_gets_comparison(client):
    d = _post(client, *ALL3).get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD", "HDYN", "MCLD"] and d["comparison"]


def test_api_foreign_ticker_is_forbidden_and_audited(client):
    r = _post(client, *BLFD, ticker="MCLD")
    assert r.status_code == 403 and r.get_json()["code"] == "FORBIDDEN"
    events = [e for e in srv._audit_recent(50) if e.get("event") == "analyze_denied"]
    assert any(e.get("detail", {}).get("ticker") == "MCLD" and e["detail"].get("kind") == "behavior" for e in events)


def test_api_bad_input_is_400(client):
    for body in ({"year": 1999}, {"assessment": "great"}, {"year": "x"}):
        assert _post(client, *ALL3, **body).status_code == 400


def test_api_year_and_filter(client):
    d = _post(client, *ALL3, ticker="MCLD", year=2024).get_json()
    assert d["year"] == 2024 and d["companies"][0]["scorecard"] is None
    d = _post(client, *ALL3, ticker="MCLD", assessment="worsened").get_json()
    rows = [r for s in d["companies"][0]["sections"] for r in s["rows"]]
    assert [r["label"] for r in rows] == ["Net revenue retention (Q4)"]


def test_api_is_audited(client):
    _post(client, *BLFD)
    ev = [e for e in srv._audit_recent(20) if e.get("event") == "analyze" and e.get("detail", {}).get("kind") == "behavior"]
    assert ev and ev[-1]["detail"]["companies"] == ["BLFD"]


def test_department_limited_user_matches_their_pdf_access(client):
    """Department limits cover the contract register only; behavior data follows ticker access."""
    d = _post(client, *DEPT).get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD"]
    assert _post(client, *DEPT, ticker="HDYN").status_code == 403


def test_small_rate_with_large_relative_move_counts(tiny):
    """8.5% churn falling to 7.7% is under a point but a 9% improvement, so it is not flat."""
    rows = _base() + [
        _row("AAA", 2024, "churn", 8.5, "%", direction="down"), _row("AAA", 2025, "churn", 7.7, "%", -0.8, "pts", "down"),
        _row("AAA", 2024, "big", 74.0, "%"), _row("AAA", 2025, "big", 74.7, "%", 0.7, "pts"),
        _row("AAA", 2024, "slip", 8.0, "%"), _row("AAA", 2025, "slip", 7.4, "%", -0.6, "pts"),
    ]
    tiny(rows)
    c = behavior.analyze({"AAA"}, Filters())["companies"][0]
    a = {r["key"]: r for s in c["sections"] for r in s["rows"]}
    assert a["churn"]["assessment"] == "improved"          # -0.8 pts but -9.4% of its level
    assert a["big"]["assessment"] == "flat"                # +0.7 pts on a high base is +0.9%
    assert a["slip"]["assessment"] == "worsened"           # -0.6 pts but -7.5% of its level, higher is better
    assert "flat" in a["big"]["why"] and "under 1 pt" in a["big"]["why"]
    assert "Fell" in a["slip"]["why"] and "Higher is better" in a["slip"]["why"]


def test_every_judged_row_explains_itself_and_concerns_have_context(tiny):
    rows = _base() + [
        _row("AAA", 2024, "churn", 10.0, "%", direction="down"), _row("AAA", 2025, "churn", 12.0, "%", 2.0, "pts", "down"),
        _row("AAA", 2024, "size", 100.0, direction="neutral"), _row("AAA", 2025, "size", 150.0, "", 50.0, "pct", "neutral"),
    ]
    tiny(rows)
    c = behavior.analyze({"AAA"}, Filters())["companies"][0]
    for s in c["sections"]:
        for r in s["rows"]:
            assert (r["why"] is not None) == (r["assessment"] is not None)
    concern = c["concerns"][0]
    assert concern["key"] == "churn" and "Revenue grew 10" in concern["context"]
