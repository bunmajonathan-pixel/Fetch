"""
test_financials.py — quarterly financial trends: PDF extraction, statistics,
permission scope, and the API.
"""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

import retrieval.auth as _auth_mod
import server as srv
from retrieval import financials
from retrieval.financials import Filters

ROOT = Path(__file__).resolve().parent.parent
IND = srv.DATA_ROOT / "individual"

# tools/ is not a package, so load the extractor by path
_spec = importlib.util.spec_from_file_location("extract_financials", ROOT / "tools" / "extract_financials.py")
ef = importlib.util.module_from_spec(_spec)
sys.modules["extract_financials"] = ef
_spec.loader.exec_module(ef)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def extracted():
    return ef.load_all()


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    with srv.app.test_client() as c:
        yield c


def _post(client, uid, pw, **body):
    r = client.post("/api/signin", json={"id": uid, "password": pw})
    assert r.status_code == 200
    return client.post("/api/analyze/financials", json=body)


BLFD = ("BLFD-01", "blfd2024")
ALL3 = ("NL-2048", "password1234")
DEPT = ("BLFD-LOG", "blfdlog2024")


def _tiny_files(tmp_path, revenues, ticker="AAA"):
    """Write 12 quarters starting Q3-2023 with the given revenues; costs are fixed ratios."""
    fin, seg = tmp_path / "f.csv", tmp_path / "s.csv"
    cols = ["ticker", "period", "year", "quarter"] + list(financials._NUMERIC)
    with fin.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        y, q = 2023, 3
        for rev in revenues:
            w.writerow({"ticker": ticker, "period": f"Q{q}-{y}", "year": y, "quarter": q,
                        "revenue": rev, "gross_profit": rev * 0.4, "operating_income": rev * 0.2,
                        "net_income": rev * 0.1, "eps": rev / 1000, "free_cash_flow": rev * 0.05})
            q += 1
            if q == 5:
                q, y = 1, y + 1
    with seg.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "period", "year", "quarter", "segment", "revenue"])
        w.writerow([ticker, "Q2-2026", 2026, 2, "Core", revenues[-1] * 0.75])
        w.writerow([ticker, "Q2-2026", 2026, 2, "Edge", revenues[-1] * 0.25])
        w.writerow([ticker, "Q2-2025", 2025, 2, "Core", revenues[-5] * 0.5])
        w.writerow([ticker, "Q2-2025", 2025, 2, "Edge", revenues[-5] * 0.5])
    return fin, seg


@pytest.fixture()
def tiny(tmp_path):
    """Swap in a tiny dataset and restore the real one afterwards."""
    saved = (financials._FIN_PATH, financials._SEG_PATH)

    def make(revenues, ticker="AAA"):
        fin, seg = _tiny_files(tmp_path, revenues, ticker)
        financials.init_financials(fin, seg)

    yield make
    financials.init_financials(*saved)


# ---------------------------------------------------------------------------
# Extraction from the PDFs
# ---------------------------------------------------------------------------

def test_every_report_reconciles(extracted):
    reports, summaries = extracted
    assert len(reports) == 36 and len(summaries) == 3
    assert ef.reconcile(reports, summaries) == []


def test_reconcile_detects_tampering(extracted):
    import copy
    reports, summaries = copy.deepcopy(extracted[0]), copy.deepcopy(extracted[1])
    reports[("BLFD", "Q2-2026")]["row"]["net_income"] = 99.0
    assert ef.reconcile(reports, summaries)


def test_known_values_from_the_pdfs(extracted):
    r = extracted[0][("BLFD", "Q2-2026")]["row"]
    assert (r["revenue"], r["gross_profit"], r["operating_income"], r["net_income"], r["eps"]) \
        == (1221.8, 389.4, 145.5, 97.6, 0.33)
    assert r["free_cash_flow"] == 132.9 and r["cash"] == 647.0
    assert extracted[0][("MCLD", "Q2-2026")]["row"]["inventory"] is None        # '—' in the PDF


def test_committed_csvs_match_the_pdfs(extracted, tmp_path, monkeypatch):
    """The CSVs in git must be exactly what the extractor produces from today's PDFs."""
    monkeypatch.setattr(ef, "OUT_FIN", tmp_path / "f.csv")
    monkeypatch.setattr(ef, "OUT_SEG", tmp_path / "s.csv")
    assert ef.main([]) == 0
    assert (tmp_path / "f.csv").read_text() == (IND / "financials.csv").read_text()
    assert (tmp_path / "s.csv").read_text() == (IND / "segments.csv").read_text()


# ---------------------------------------------------------------------------
# Statistics (exact arithmetic on a tiny dataset)
# ---------------------------------------------------------------------------

LINEAR = [100 + 10 * i for i in range(12)]          # 100, 110, ... 210


def test_growth_margins_and_ttm(tiny):
    tiny(LINEAR)
    c = financials.analyze({"AAA"}, Filters())["companies"][0]
    last = c["latest"]
    assert last["period"] == "Q2-2026" and last["revenue"] == 210
    assert last["revenue_qoq"] == round(100 * 10 / 200, 2)           # 5.0
    assert last["revenue_yoy"] == round(100 * (210 - 170) / 170, 2)  # 4 quarters earlier = 170
    assert (last["gross_margin"], last["operating_margin"], last["net_margin"]) == (40.0, 20.0, 10.0)
    assert last["ttm_revenue"] == round(sum(LINEAR[-4:]), 1) == 780.0
    # the first three quarters have no TTM, the first four no YoY
    first = c["series"][0]
    assert first["ttm_revenue"] is None and first["revenue_yoy"] is None and first["revenue_qoq"] is None
    assert c["series"][4]["revenue_yoy"] == round(100 * (140 - 100) / 100, 2)


def test_trend_and_cagr(tiny):
    tiny(LINEAR)
    s = financials.analyze({"AAA"}, Filters())["companies"][0]["stats"]
    assert s["trend_slope_per_qtr"] == 10.0 and s["trend_r2"] == 1.0
    ttm_now, ttm_2y = sum(LINEAR[-4:]), sum(LINEAR[0:4])
    assert s["revenue_cagr_2y_pct"] == round(100 * ((ttm_now / ttm_2y) ** 0.5 - 1), 2)


def test_flat_series_has_zero_trend_and_no_division_errors(tiny):
    tiny([100] * 12)
    s = financials.analyze({"AAA"}, Filters())["companies"][0]["stats"]
    assert s["trend_slope_per_qtr"] == 0.0 and s["trend_r2"] == 0.0
    assert s["qoq_stdev_pct"] == 0.0 and s["qoq_mean_pct"] == 0.0


def test_seasonality_uses_complete_years_only(tiny):
    # 2024 = 100,100,100,200 and 2025 = same; 2023 (Q3,Q4) and 2026 (Q1,Q2) are incomplete
    tiny([50, 50] + [100, 100, 100, 200] * 2 + [50, 50])
    z = financials.analyze({"AAA"}, Filters())["companies"][0]["stats"]["seasonality"]
    assert z == {"Q1": 0.800, "Q2": 0.800, "Q3": 0.800, "Q4": 1.600}


def test_window_keeps_growth_computed_on_full_history(tiny):
    tiny(LINEAR)
    c = financials.analyze({"AAA"}, Filters(from_period="Q3-2025"))["companies"][0]
    assert [r["period"] for r in c["series"]] == ["Q3-2025", "Q4-2025", "Q1-2026", "Q2-2026"]
    assert c["series"][0]["revenue_yoy"] is not None                  # reaches back before the window
    assert c["indexed"][0]["value"] == 100.0                          # but the index restarts at the window


def test_segment_mix_and_growth(tiny):
    tiny(LINEAR)
    segs = financials.analyze({"AAA"}, Filters())["companies"][0]["segments"]
    assert [g["name"] for g in segs] == ["Core", "Edge"]
    assert segs[0]["share_pct"] == 75.0 and segs[1]["share_pct"] == 25.0
    assert segs[0]["yoy_pct"] == round(100 * (210 * 0.75 - 170 * 0.5) / (170 * 0.5), 2)


def test_real_data_matches_the_reports():
    """Computed YoY agrees with the YoY printed in the PDF (BLFD Q2 2026: +2.2%, +12.3%, +16.2%).

    The PDF rounds from unrounded data while we divide figures already rounded to $0.1M,
    so a difference of a tenth of a point is expected and allowed."""
    financials.init_financials(IND / "financials.csv", IND / "segments.csv")
    c = financials.analyze({"BLFD"}, Filters())["companies"][0]
    assert c["latest"]["revenue_yoy"] == pytest.approx(2.2, abs=0.1)
    assert c["latest"]["operating_income_yoy"] == pytest.approx(12.3, abs=0.1)
    assert c["latest"]["net_income_yoy"] == pytest.approx(16.2, abs=0.1)
    assert sum(g["revenue"] for g in c["segments"]) == pytest.approx(c["latest"]["revenue"], abs=0.3)


# ---------------------------------------------------------------------------
# Filters and scope
# ---------------------------------------------------------------------------

def test_parse_filters():
    f = financials.parse_filters({"ticker": "blfd", "from": "q1 2025", "to": "Q4-2025", "metric": "eps"})
    assert (f.ticker, f.from_period, f.to_period, f.metric) == ("BLFD", "Q1-2025", "Q4-2025", "eps")
    assert financials.parse_filters({"from": "all"}).from_period is None
    for bad in ({"from": "2025"}, {"from": "Q5-2025"}, {"metric": "profit"},
                {"from": "Q4-2025", "to": "Q1-2025"}):
        with pytest.raises(ValueError):
            financials.parse_filters(bad)


def test_empty_scope_returns_nothing():
    financials.init_financials(IND / "financials.csv", IND / "segments.csv")
    r = financials.analyze(set(), Filters())
    assert r["companies"] == [] and r["ranking"] == [] and r["periods"] == []


def test_out_of_scope_company_never_appears_in_comparisons(tiny):
    tiny(LINEAR)                                     # only AAA exists
    assert financials.analyze({"ZZZ"}, Filters())["companies"] == []
    r = financials.analyze({"AAA", "ZZZ"}, Filters())
    assert [c["ticker"] for c in r["companies"]] == ["AAA"] and r["ranking"] == []


def test_ranking_uses_only_scoped_companies():
    financials.init_financials(IND / "financials.csv", IND / "segments.csv")
    two = financials.analyze({"BLFD", "HDYN"}, Filters())
    names = {o["ticker"] for r in two["ranking"] for o in r["order"]}
    assert names == {"BLFD", "HDYN"}
    assert financials.analyze({"BLFD"}, Filters())["ranking"] == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_requires_sign_in(client):
    assert client.post("/api/analyze/financials", json={}).status_code == 401


def test_api_single_company_user_sees_only_their_company(client):
    r = _post(client, *BLFD)
    assert r.status_code == 200
    d = r.get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD"]
    assert [c["ticker"] for c in d["scope"]["companies"]] == ["BLFD"]
    assert d["ranking"] == [] and d["as_of"] == "Q2-2026" and len(d["periods"]) == 12
    body = r.get_data(as_text=True)
    assert "HDYN" not in body and "MCLD" not in body and "Robotics" not in body


def test_api_multi_company_user_gets_comparison(client):
    d = _post(client, *ALL3).get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD", "HDYN", "MCLD"]
    assert d["ranking"]


def test_api_foreign_ticker_is_forbidden_and_audited(client):
    r = _post(client, *BLFD, ticker="HDYN")
    assert r.status_code == 403 and r.get_json()["code"] == "FORBIDDEN"
    events = [e for e in srv._audit_recent(50) if e.get("event") == "analyze_denied"]
    assert any(e.get("detail", {}).get("ticker") == "HDYN" for e in events)


def test_api_bad_input_is_400(client):
    for body in ({"metric": "nope"}, {"from": "yesterday"}, {"from": "Q4-2025", "to": "Q1-2025"}):
        assert _post(client, *ALL3, **body).status_code == 400


def test_api_window_and_metric(client):
    d = _post(client, *ALL3, ticker="HDYN", **{"from": "Q1-2025", "to": "Q4-2025"}, metric="gross_margin").get_json()
    assert d["periods"] == ["Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025"]
    assert d["metric"]["key"] == "gross_margin" and [c["ticker"] for c in d["companies"]] == ["HDYN"]


def test_department_limited_user_matches_their_pdf_access(client):
    """Department limits cover the contract register only; company financials follow ticker access."""
    d = _post(client, *DEPT).get_json()
    assert [c["ticker"] for c in d["companies"]] == ["BLFD"]
    assert _post(client, *DEPT, ticker="MCLD").status_code == 403


# ---------------------------------------------------------------------------
# Improved / worse / flat flags
# ---------------------------------------------------------------------------

def _score(tiny, revenues, **filters):
    tiny(revenues)
    c = financials.analyze({"AAA"}, financials.parse_filters(filters))["companies"][0]
    return c["scorecard"], {m["key"]: m for m in c["scorecard"]["measures"]}


def test_growing_company_is_flagged_improved_and_ratios_are_flat(tiny):
    sc, by = _score(tiny, [100 + 10 * i for i in range(12)])
    assert sc["period"] == "Q2-2026" and sc["prior_period"] == "Q2-2025" and sc["basis"] == "yoy"
    assert by["revenue"]["assessment"] == "improved" and by["net_income"]["assessment"] == "improved"
    assert by["gross_margin"]["assessment"] == "flat"          # fixed cost ratios: 0 pts of movement
    assert sc["tracked"] == sc["improved"] + sc["worsened"] + sc["flat"]
    assert [m["key"] for m in sc["measures"] if m["assessment"] == "worsened"] == ["segment:Edge"]   # Edge shrank 85 -> 52.5
    assert "flat" in by["gross_margin"]["why"]


def test_shrinking_company_is_flagged_worse_and_says_why(tiny):
    sc, by = _score(tiny, [200 - 5 * i for i in range(12)])
    assert by["revenue"]["assessment"] == "worsened" and by["free_cash_flow"]["assessment"] == "worsened"
    assert "Higher is better" in by["revenue"]["why"] and "worsened" in by["revenue"]["why"]
    assert [m["key"] for m in sc["concerns"]] and all(m["assessment"] == "worsened" for m in sc["concerns"])


def test_a_small_move_is_flat_not_improved(tiny):
    sc, by = _score(tiny, [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 101])   # +1% year on year
    assert by["revenue"]["assessment"] == "flat"


def test_qoq_basis_compares_with_the_previous_quarter(tiny):
    revs = [100] * 10 + [120, 90]
    yoy, _ = _score(tiny, revs)
    qoq, by = _score(tiny, revs, basis="qoq")
    assert qoq["prior_period"] == "Q1-2026" and qoq["basis"] == "qoq"
    assert by["revenue"]["assessment"] == "worsened"            # 120 -> 90
    assert yoy["prior_period"] == "Q2-2025"


def test_segments_are_flagged_only_when_present_in_both_quarters(tiny):
    _, by = _score(tiny, [100 + 10 * i for i in range(12)])
    assert by["segment:Core"]["assessment"] == "improved"        # 75% share now, 50% share a year ago
    assert by["segment:Edge"]["assessment"] == "worsened"        # 85.0 a year ago, 52.5 now
    assert by["segment:Core"]["group"] == "Segments"


def test_debt_and_sga_share_count_a_fall_as_better():
    keys = {k: d for k, _, _, _, d, _ in financials.SCORED}
    assert keys["total_debt"] == "down" and keys["sga_pct"] == "down" and keys["revenue"] == "up"


def test_assessment_filter_narrows_the_list_but_not_the_tally(tiny):
    all_ = _score(tiny, [100 + 10 * i for i in range(12)])[0]
    only = _score(tiny, [100 + 10 * i for i in range(12)], assessment="flat")[0]
    assert only["measures"] and all(m["assessment"] == "flat" for m in only["measures"])
    assert len(only["measures"]) == all_["flat"] < len(all_["measures"])
    assert (only["improved"], only["worsened"], only["flat"]) == (all_["improved"], all_["worsened"], all_["flat"])


def test_history_has_a_tally_for_each_quarter_with_a_comparison(tiny):
    sc, _ = _score(tiny, [100 + 10 * i for i in range(12)])
    assert [h["period"] for h in sc["history"]][0] == "Q3-2024"      # first quarter with a year-earlier quarter
    assert len(sc["history"]) == 8
    qoq = _score(tiny, [100 + 10 * i for i in range(12)], basis="qoq")[0]
    assert len(qoq["history"]) == 11


def test_no_scorecard_without_a_comparison_quarter(tiny):
    tiny([100 + 10 * i for i in range(12)])
    c = financials.analyze({"AAA"}, financials.parse_filters({"to": "Q3-2023"}))["companies"][0]    # the very first quarter
    assert c["scorecard"] is None


def test_bad_flag_filters_are_rejected():
    with pytest.raises(ValueError):
        financials.parse_filters({"assessment": "great"})
    with pytest.raises(ValueError):
        financials.parse_filters({"basis": "weekly"})


def test_real_data_has_worse_and_flat_flags(client):
    d = _post(client, *ALL3).get_json()
    cards = {c["ticker"]: c["scorecard"] for c in d["companies"]}
    assert cards["HDYN"]["worsened"] >= 1                          # free cash flow slipped for HDYN
    assert all(c["flat"] >= 1 for c in cards.values())
    assert sum(c["worsened"] + c["flat"] for c in cards.values()) >= 8
    assert d["filters"]["basis"] == "yoy" and d["filters"]["assessment"] == "all"


def test_api_flag_filters_and_scope(client):
    d = _post(client, *ALL3, ticker="HDYN", assessment="worsened", basis="qoq").get_json()
    sc = d["companies"][0]["scorecard"]
    assert sc["basis"] == "qoq" and sc["measures"] and all(m["assessment"] == "worsened" for m in sc["measures"])
    assert _post(client, *ALL3, assessment="bogus").status_code == 400
    assert _post(client, *ALL3, basis="bogus").status_code == 400
    one = _post(client, *BLFD).get_json()
    assert [c["ticker"] for c in one["companies"]] == ["BLFD"]       # no other company's flags leak into scope
