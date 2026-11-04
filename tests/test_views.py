"""
test_views.py — saved views: validation, per-user isolation, and that a saved view never
widens what its owner may see.
"""
import pytest

import server as srv
from retrieval import auth as _auth_mod
from retrieval import views

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


@pytest.fixture(autouse=True)
def clean_views():
    for u in ("NL-2048", "BLFD-01", "BLFD-LOG"):
        for v in views.list_views(u):
            views.delete(u, v["id"])


def _in(client, creds):
    assert client.post("/api/signin", json={"id": creds[0], "password": creds[1]}).status_code == 200


def _save(client, **body):
    return client.post("/api/views", json=body)


def test_requires_sign_in(client):
    assert client.get("/api/views").status_code == 401
    assert client.post("/api/views", json={"name": "x", "screen": "company"}).status_code == 401
    assert client.delete("/api/views/1").status_code == 401


def test_save_list_replace_delete(client):
    _in(client, ALL3)
    r = _save(client, name="Q4 sole-source", screen="contracts",
              filters={"quarter": "Q4-2026", "sole_source": "yes", "junk": "x", "owner": ""})
    assert r.status_code == 201
    v = r.get_json()
    assert v["filters"] == {"quarter": "Q4-2026", "sole_source": "yes"}          # junk and blanks dropped
    again = _save(client, name="Q4 sole-source", screen="contracts", filters={"quarter": "Q1-2027"}).get_json()
    assert again["id"] == v["id"] and again["filters"] == {"quarter": "Q1-2027"}   # same name replaces
    assert [x["name"] for x in client.get("/api/views").get_json()["views"]] == ["Q4 sole-source"]
    assert client.delete(f"/api/views/{v['id']}").status_code == 200
    assert client.get("/api/views").get_json()["views"] == []
    assert client.delete(f"/api/views/{v['id']}").status_code == 404


def test_same_name_on_different_screens_is_allowed(client):
    _in(client, ALL3)
    assert _save(client, name="Watch", screen="company", filters={"ticker": "MCLD"}).status_code == 201
    assert _save(client, name="Watch", screen="behavior", filters={"year": 2025, "assessment": "worsened"}).status_code == 201
    got = client.get("/api/views").get_json()["views"]
    assert {(v["screen"], v["name"]) for v in got} == {("company", "Watch"), ("behavior", "Watch")}
    assert [v["filters"] for v in got if v["screen"] == "behavior"] == [{"year": "2025", "assessment": "worsened"}]


@pytest.mark.parametrize("body", [
    {"name": "", "screen": "company"},
    {"name": "x" * 61, "screen": "company"},
    {"name": "x", "screen": "nope"},
    {"name": "x", "screen": "company", "filters": ["a"]},
    {"name": "x", "screen": "company", "filters": {"ticker": "ZZZZ"}},
    {"name": "x", "screen": "behavior", "filters": {"year": 1999}},
    {"name": "x", "screen": "behavior", "filters": {"assessment": "great"}},
    {"name": "x", "screen": "financials", "filters": {"metric": "vibes"}},
    {"name": "x", "screen": "contracts", "filters": {"status": "Imaginary"}},
    {"name": "x", "screen": "company", "filters": {"ticker": {"a": 1}}},
])
def test_bad_input_is_400(client, body):
    _in(client, ALL3)
    r = _save(client, **body)
    assert r.status_code == 400 and r.get_json()["code"] == "BAD_REQUEST"


def test_limit_per_user(client):
    _in(client, ALL3)
    for i in range(views.MAX_PER_USER):
        assert _save(client, name=f"v{i}", screen="company").status_code == 201
    assert _save(client, name="one too many", screen="company").status_code == 400
    assert _save(client, name="v0", screen="company", filters={"ticker": "BLFD"}).status_code == 201    # replacing is fine


def test_views_are_private_to_their_owner(client):
    _in(client, ALL3)
    vid = _save(client, name="mine", screen="company").get_json()["id"]
    client.post("/api/signout", json={})
    _in(client, BLFD)
    assert client.get("/api/views").get_json()["views"] == []
    assert client.delete(f"/api/views/{vid}").status_code == 404
    client.post("/api/signout", json={})
    _in(client, ALL3)
    assert len(client.get("/api/views").get_json()["views"]) == 1                 # untouched


def test_a_saved_view_never_widens_access(client):
    """Saving a filter for a company is allowed, but running it is still checked against the account's scope."""
    _in(client, BLFD)
    assert _save(client, name="other co", screen="company", filters={"ticker": "MCLD"}).status_code == 201
    v = client.get("/api/views").get_json()["views"][0]
    assert client.post("/api/analyze/company", json=v["filters"]).status_code == 403


def test_saved_view_filters_run_against_the_real_endpoint(client):
    _in(client, ALL3)
    f = _save(client, name="Worse in MCLD", screen="behavior",
              filters={"ticker": "MCLD", "year": 2025, "assessment": "worsened"}).get_json()["filters"]
    d = client.post("/api/analyze/behavior", json=f).get_json()
    assert [c["ticker"] for c in d["companies"]] == ["MCLD"] and d["filters"]["assessment"] == "worsened"


def test_save_and_delete_are_audited(client):
    _in(client, ALL3)
    vid = _save(client, name="audited", screen="company").get_json()["id"]
    client.delete(f"/api/views/{vid}")
    events = [e["event"] for e in srv._audit_recent(20)]
    assert "view_saved" in events and "view_deleted" in events
