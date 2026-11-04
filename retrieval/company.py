"""
company.py — one page per company that joins the three analytics.

Financials (quarterly reports), customer behavior (annual reports) and supplier contracts
(the register) are each analysed on their own elsewhere.  This module only *combines* what
those analyses already return, and raises a signal where two sources say something
together that neither says alone, for example margins slipping while supplier renewals
have not started.

It does no data loading of its own, so the permission rules that govern each source
(ticker access for financials and behavior; ticker plus department for contracts) are
applied by the caller before anything arrives here.  A department-limited account passes
``partial=True``: contract figures then cover only its departments, so signals that
compare contract totals with company-wide numbers are not produced.
"""
from __future__ import annotations

# Thresholds in one place so the rules are easy to read and to change.
SOLE_SOURCE_SHARE_PCT = 20.0     # sole-source value above this share of active spend is a watch item
TOP3_SHARE_PCT = 40.0            # top three suppliers above this share of active spend
MARGIN_SLIP_BPS = 0              # a year-on-year gross-margin change below this counts as slipping
GROWTH_GAP_PTS = 2.0             # latest-quarter growth vs. last full year, difference worth a note
NEAR_QUARTERS = 2                # "near term" for renewals = this many upcoming quarters

LEVELS = ("watch", "good", "info")


def _money_k(v: float) -> str:
    return f"${v / 1000:,.1f}M" if v >= 1000 else f"${v:,.0f}K"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _expire(n: int) -> str:
    return "expires" if n == 1 else "expire"


def _signal(sid, level, title, detail, sources, evidence):
    return {"id": sid, "level": level, "title": title, "detail": detail,
            "sources": sources, "evidence": [{"label": l, "value": v} for l, v in evidence]}


def _near_unstarted(con: dict) -> tuple[int, float]:
    """Contracts and annual value expiring in the next NEAR_QUARTERS whose renewal has not started."""
    rows = con["outlook"][:NEAR_QUARTERS]
    return (sum(o["not_started"]["contracts"] for o in rows), sum(o["not_started"]["value_k"] for o in rows))


def signals(fin: dict, beh: dict, con: dict, partial: bool) -> list[dict]:
    out: list[dict] = []
    lat, bps = fin["latest"], fin["margin_change_bps_yoy"]
    k = con["kpis"]
    n_contracts, v_unstarted = _near_unstarted(con)

    # 1. Margin vs. renewals that have not started  (financials + contracts)
    gm = bps.get("gross_margin")
    if gm is not None and k["contracts"]:
        ev = [(f"Gross margin, {lat['period']}", f"{lat['gross_margin']:.1f}% ({gm:+d} bp vs. last year)"),
              (f"Renewals not started, next {NEAR_QUARTERS} quarters", f"{_plural(n_contracts, 'contract')}, {_money_k(v_unstarted)} a year")]
        if gm < MARGIN_SLIP_BPS and n_contracts:
            out.append(_signal("margin_vs_renewals", "watch", "Margin is slipping and near-term renewals are open",
                               f"Gross margin is {abs(gm)} bp lower than a year ago and {_plural(n_contracts, 'supplier contract')} "
                               f"({_money_k(v_unstarted)} a year) {_expire(n_contracts)} within {NEAR_QUARTERS} quarters with no renewal started. "
                               "Starting those early gives the most room to protect cost.", ["Financials", "Contracts"], ev))
        elif gm >= MARGIN_SLIP_BPS and n_contracts:
            out.append(_signal("margin_vs_renewals", "info", "Margin is holding; renewals still need to start",
                               f"Gross margin is {gm} bp higher than a year ago, but {_plural(n_contracts, 'supplier contract')} "
                               f"({_money_k(v_unstarted)} a year) {_expire(n_contracts)} within {NEAR_QUARTERS} quarters with no renewal started.",
                               ["Financials", "Contracts"], ev))
        elif gm >= MARGIN_SLIP_BPS:
            out.append(_signal("margin_vs_renewals", "good", "Margin is up and near-term renewals are in hand",
                               f"Gross margin is {gm} bp higher than a year ago and no contract expiring in the next "
                               f"{NEAR_QUARTERS} quarters is waiting on a renewal.", ["Financials", "Contracts"], ev))

    # 2. Contracts that are about to lapse  (contracts)
    if k["at_risk"]["contracts"]:
        out.append(_signal("lapsing", "watch", "Contracts about to lapse without a renewal",
                           f"{_plural(k['at_risk']['contracts'], 'active contract')}, {_money_k(k['at_risk']['value_k'])} a year. "
                           f"Rule: {con['risk_rule']['text'].lower()}.", ["Contracts"],
                           [("At risk", f"{_plural(k['at_risk']['contracts'], 'contract')}, {_money_k(k['at_risk']['value_k'])}")]))

    # 3. Sole-source and supplier concentration  (contracts; share needs the whole register)
    if not partial and k["value_k"]:
        ss = k["sole_source"]
        share = round(100 * ss["value_k"] / k["value_k"], 1)
        if share >= SOLE_SOURCE_SHARE_PCT or k["top3_share_pct"] >= TOP3_SHARE_PCT:
            parts = []
            if share >= SOLE_SOURCE_SHARE_PCT:
                parts.append(f"{share}% of active spend ({_money_k(ss['value_k'])}) has no alternative supplier")
            if k["top3_share_pct"] >= TOP3_SHARE_PCT:
                parts.append(f"the top three suppliers hold {k['top3_share_pct']}% of spend")
            out.append(_signal("supplier_dependence", "watch", "Dependence on a few suppliers",
                               ("; ".join(parts))[0].upper() + ("; ".join(parts))[1:] + ".", ["Contracts"],
                               [("Sole-source share", f"{share}%"), ("Top-3 supplier share", f"{k['top3_share_pct']}%")]))

    # 4. Customer health vs. revenue  (behavior + financials)
    if beh.get("scorecard"):
        sc, worse = beh["scorecard"], beh["concerns"]
        ev = [(f"Customer measures, {beh['year']}", f"{sc['improved']} improved, {sc['worsened']} worse, {sc['flat']} flat"),
              (f"Revenue growth, {lat['period']}", f"{lat['revenue_yoy']:+.1f}% vs. last year")]
        if worse:
            names = ", ".join(r["label"] for r in worse)
            out.append(_signal("customer_health", "watch", "A customer measure is getting worse",
                               f"{names} got worse in {beh['year']} (revenue growth is still {lat['revenue_yoy']:+.1f}% in the "
                               f"latest quarter). Worth checking before it shows up in revenue.", ["Customer behavior", "Financials"],
                               ev + [(r["label"], f"{r['prior']:g} → {r['value']:g}") for r in worse]))
        elif sc["improved"] > sc["flat"] and lat["revenue_yoy"] > 0:
            out.append(_signal("customer_health", "good", "Customers and revenue are moving the same way",
                               f"{sc['improved']} of {sc['tracked']} customer measures improved in {beh['year']} and revenue is growing.",
                               ["Customer behavior", "Financials"], ev))

    # 5. Latest-quarter growth vs. the last full year  (financials + behavior)
    g = beh.get("growth")
    if g and g["revenue_pct"] is not None:
        gap = round(lat["revenue_yoy"] - g["revenue_pct"], 1)
        if abs(gap) >= GROWTH_GAP_PTS:
            word = "slowed" if gap < 0 else "sped up"
            out.append(_signal("growth_trend", "watch" if gap < 0 else "good", f"Revenue growth has {word}",
                               f"Revenue grew {g['revenue_pct']:+.1f}% in {beh['year']} but {lat['revenue_yoy']:+.1f}% in {lat['period']} "
                               f"vs. a year earlier. Over {beh['year']}, {g['customers_pct']:+.1f}% came from more customers and "
                               f"{g['per_customer_pct']:+.1f}% from more revenue per customer.",
                               ["Financials", "Customer behavior"],
                               [(f"Full year {beh['year']}", f"{g['revenue_pct']:+.1f}%"), (lat["period"], f"{lat['revenue_yoy']:+.1f}%")]))

    return out


def weight(fin: dict, con: dict, partial: bool) -> dict | None:
    """Active supplier spend against trailing-12-month revenue.  Not meaningful for a partial view."""
    if partial or not con["kpis"]["contracts"]:
        return None
    ttm = fin["latest"]["ttm_revenue"]
    spend = con["kpis"]["active_spend_k"] / 1000
    return {"active_spend_m": round(spend, 1), "ttm_revenue_m": ttm, "pct": round(100 * spend / ttm, 1)}


def profile(ticker: str, name: str, fin: dict, beh: dict, con: dict, partial: bool = False) -> dict:
    lat = fin["latest"]
    sigs = signals(fin, beh, con, partial)
    order = {"watch": 0, "info": 1, "good": 2}
    sigs.sort(key=lambda s: order[s["level"]])
    return {
        "ticker": ticker, "name": name, "partial_contracts": partial,
        "financials": {"period": lat["period"], "revenue": lat["revenue"], "revenue_yoy": lat["revenue_yoy"],
                       "gross_margin": lat["gross_margin"], "operating_margin": lat["operating_margin"],
                       "margin_change_bps_yoy": fin["margin_change_bps_yoy"], "net_income": lat["net_income"],
                       "free_cash_flow": lat["free_cash_flow"], "ttm_revenue": lat["ttm_revenue"]},
        "behavior": {"year": beh["year"], "revenue_pct": (beh.get("growth") or {}).get("revenue_pct"),
                     "customers": beh["customers"], "growth": beh.get("growth"), "scorecard": beh.get("scorecard"),
                     "concerns": [{"label": r["label"], "prior": r["prior"], "value": r["value"], "unit": r["unit"],
                                   "change": r["change"], "change_kind": r["change_kind"]} for r in beh["concerns"]],
                     "rollforward": beh.get("rollforward")},
        "contracts": {"kpis": con["kpis"], "outlook": con["outlook"], "renewal_pipeline": con["renewal_pipeline"],
                      "risk_rule": con["risk_rule"], "as_of": con["as_of"]},
        "weight": weight(fin, con, partial),
        "signals": sigs,
        "counts": {lv: sum(s["level"] == lv for s in sigs) for lv in LEVELS},
    }
