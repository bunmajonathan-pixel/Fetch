"""
alerts.py — one "needs attention" list across companies and sources.

Nothing is loaded here.  The caller builds each company's profile (``company.profile``) under
the account's own permissions, and this module only collects what those profiles already
say into a single ranked list:

* every cross-source signal the company page raises, and
* "surprises": a measure whose latest quarter landed outside the range that the estimate
  method (see ``financials._forecast``) would have given from the history before it.

Because it only reads the profiles it is given, it cannot show a company, or a contract
outside a department-limited account, that the caller could not already open.
"""
from __future__ import annotations

LEVELS = ("watch", "info", "good")
_ORDER = {lv: i for i, lv in enumerate(LEVELS)}
_SCREEN = {"Financials": "financials", "Contracts": "contracts", "Customer behavior": "behavior"}


def _money(v: float, unit: str) -> str:
    if unit == "$":
        return f"${v:,.2f}"
    return f"${v / 1000:,.2f}B" if abs(v) >= 1000 else f"${v:,.1f}M"


def _surprise_alert(p: dict) -> dict | None:
    items = (p.get("financials") or {}).get("surprises") or []
    if not items:
        return None
    below = [m for m in items if m["direction"] == "below"]
    period = items[0]["period"]
    level = "watch" if below else "good"
    names = ", ".join(m["label"] for m in (below or items))
    if below:
        title = f"{period} results came in below the range the trend implied"
        detail = (f"{names} {'was' if len(below) == 1 else 'were'} below the range an estimate "
                  f"from earlier quarters would give. Worth reading the report for the cause.")
    else:
        title = f"{period} results came in above the range the trend implied"
        detail = f"{names} beat the range an estimate from earlier quarters would give."
    ev = [{"label": f"{m['label']}, {m['period']}",
           "value": f"{_money(m['actual'], m['unit'])} vs. {_money(m['low'], m['unit'])} – {_money(m['high'], m['unit'])} "
                    f"({m['miss_pct']:+.1f}% from the estimate)"} for m in items]
    return {"id": f"{p['ticker']}:surprise", "kind": "surprise", "level": level, "ticker": p["ticker"], "name": p["name"],
            "title": title, "detail": detail, "sources": ["Financials"], "evidence": ev, "screen": "financials"}


def build(profiles: list[dict]) -> dict:
    alerts: list[dict] = []
    for p in profiles:
        s = _surprise_alert(p)
        if s:
            alerts.append(s)
        for sig in p["signals"]:
            alerts.append({"id": f"{p['ticker']}:{sig['id']}", "kind": "signal", "level": sig["level"],
                           "ticker": p["ticker"], "name": p["name"], "title": sig["title"], "detail": sig["detail"],
                           "sources": sig["sources"], "evidence": sig["evidence"],
                           "screen": _SCREEN.get(sig["sources"][0], "company")})
    alerts.sort(key=lambda a: (_ORDER[a["level"]], a["kind"] != "surprise", a["ticker"], a["id"]))
    return {
        "alerts": alerts,
        "counts": {lv: sum(a["level"] == lv for a in alerts) for lv in LEVELS},
        "companies": [{"ticker": p["ticker"], "name": p["name"],
                       **{lv: sum(a["level"] == lv for a in alerts if a["ticker"] == p["ticker"]) for lv in LEVELS}}
                      for p in profiles],
    }
