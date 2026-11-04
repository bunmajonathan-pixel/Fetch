"""
test_admin.py — account administration and the audit view: who may use it, what it
validates, and the rules that stop an administrator locking everyone out.
"""
import pytest

import server as srv
from retrieval import auth as _auth_mod
from retrieval import db_users

ADMIN = ("ADM-01", "admin2024")
ALL3 = ("NL-2048", "password1234")
BLFD = ("BLFD-01", "blfd2024")
GOOD_PW = "a-long-enough-pw"


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    srv._login_attempts.clear()
    srv._rate_store.clear()
    _auth_mod._SESSIONS.clear()
    created = []
    with srv.app.test_client() as c:
        c.created = created
        yield c
    for u in created:                      # leave the shared test database as we found it
        db_users.delete_user(u)
    db_users.update_user("ADM-01", is_admin=True)


def _in(client, creds):
    r = client.post("/api/signin", json={"id": creds[0], "password": creds[1]})
    assert r.status_code == 200
    return r


def _make(client, username="NEW-01", **kw):
    body = {"username": username, "password": GOOD_PW, "document_ids": ["BLFD"], **kw}
    r = client.post("/api/admin/users", json=body)
    if r.status_code == 201:
        client.created.append(username)
    return r


def test_signin_reports_admin_flag(client):
    assert _in(client, ADMIN).get_json()["admin"] is True
    client.post("/api/signout", json={})
    assert _in(client, ALL3).get_json()["admin"] is False


def test_admin_account_manages_users_but_reads_no_company_data(client):
    _in(client, ADMIN)
    assert client.post("/api/analyze/financials", json={}).get_json()["companies"] == []
    assert client.post("/api/analyze/company", json={}).get_json()["companies"] == []
    assert client.post("/api/analyze/financials", json={"ticker": "BLFD"}).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/users"), ("post", "/api/admin/users"), ("patch", "/api/admin/users/BLFD-01"),
    ("delete", "/api/admin/users/BLFD-01"), ("post", "/api/admin/users/BLFD-01/unlock"), ("get", "/api/admin/audit"),
])
def test_everything_needs_sign_in_and_admin(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 401
    _in(client, ALL3)
    assert getattr(client, method)(path, json={}).status_code == 403
    assert any(e["event"] == "admin_denied" and e["username"] == "NL-2048" for e in srv._audit_recent(50))


def test_list_users_has_no_password_material(client):
    _in(client, ADMIN)
    r = client.get("/api/admin/users")
    d = r.get_json()
    names = {u["username"]: u for u in d["users"]}
    assert {"ADM-01", "BLFD-01", "NL-2048", "BLFD-LOG"} <= set(names)
    assert names["BLFD-LOG"]["owners"] == ["Logistics"] and names["ADM-01"]["is_admin"]
    body = r.get_data(as_text=True)
    assert "password" not in body.replace("min_password", "") and "$2b$" not in body
    assert "Logistics" in d["options"]["departments"]["BLFD"]


def test_create_user_then_they_can_sign_in_with_exactly_that_scope(client):
    _in(client, ADMIN)
    r = _make(client, "NEW-LOG", owners=["Logistics"])
    assert r.status_code == 201 and r.get_json()["owners"] == ["Logistics"]
    client.post("/api/signout", json={})
    _in(client, ("NEW-LOG", GOOD_PW))
    d = client.post("/api/analyze/contracts", json={}).get_json()
    assert {c["ticker"] for c in d["scope"]["companies"]} == {"BLFD"} and d["scope"]["owners"] == ["Logistics"]
    assert client.post("/api/analyze/financials", json={"ticker": "HDYN"}).status_code == 403


@pytest.mark.parametrize("body", [
    {"username": "x", "password": GOOD_PW},                                           # too short
    {"username": "bad name!", "password": GOOD_PW},
    {"username": "OK-1", "password": "short"},
    {"username": "OK-2", "password": "OK-2"},
    {"username": "OK-3", "password": GOOD_PW, "document_ids": ["ZZZZ"]},
    {"username": "OK-4", "password": GOOD_PW, "document_ids": "BLFD"},
    {"username": "OK-5", "password": GOOD_PW, "document_ids": ["BLFD"], "owners": ["Engineering"]},   # not a BLFD department
    {"username": "OK-6", "password": GOOD_PW, "document_ids": ["BLFD"], "owners": "Logistics"},
    {"username": "BLFD-01", "password": GOOD_PW, "document_ids": ["BLFD"]},                           # already exists
])
def test_create_validation(client, body):
    _in(client, ADMIN)
    r = client.post("/api/admin/users", json=body)
    assert r.status_code == 400 and r.get_json()["code"] == "BAD_REQUEST"
    assert db_users.get_user(body["username"]) is None or body["username"] == "BLFD-01"


def test_update_scope_applies_immediately(client):
    _in(client, ADMIN)
    _make(client, "NEW-02")
    other = srv.app.test_client()
    other.post("/api/signin", json={"id": "NEW-02", "password": GOOD_PW})
    assert other.post("/api/analyze/financials", json={"ticker": "HDYN"}).status_code == 403
    r = client.patch("/api/admin/users/NEW-02", json={"document_ids": ["BLFD", "HDYN"]})
    assert r.status_code == 200 and r.get_json()["document_ids"] == ["BLFD", "HDYN"]
    assert other.post("/api/analyze/financials", json={"ticker": "HDYN"}).status_code == 200      # same session, new scope


def test_update_department_limit_can_be_removed_and_is_validated(client):
    _in(client, ADMIN)
    _make(client, "NEW-03", owners=["Logistics"])
    assert client.patch("/api/admin/users/NEW-03", json={"owners": None}).get_json()["owners"] is None
    assert client.patch("/api/admin/users/NEW-03", json={"owners": ["Logistics"]}).get_json()["owners"] == ["Logistics"]
    assert client.patch("/api/admin/users/NEW-03", json={"owners": ["Nonsense"]}).status_code == 400
    # moving the account to another company leaves a department that company does not have
    assert client.patch("/api/admin/users/NEW-03", json={"document_ids": ["HDYN"]}).status_code == 400
    assert client.patch("/api/admin/users/NEW-03", json={"document_ids": ["HDYN"], "owners": None}).status_code == 200


def test_password_change_ends_that_users_sessions(client):
    _in(client, ADMIN)
    _make(client, "NEW-04")
    other = srv.app.test_client()
    other.post("/api/signin", json={"id": "NEW-04", "password": GOOD_PW})
    assert other.post("/api/analyze/company", json={}).status_code == 200
    assert client.patch("/api/admin/users/NEW-04", json={"password": "another-long-pw"}).status_code == 200
    assert other.post("/api/analyze/company", json={}).status_code == 401
    assert other.post("/api/signin", json={"id": "NEW-04", "password": GOOD_PW}).status_code == 401
    assert other.post("/api/signin", json={"id": "NEW-04", "password": "another-long-pw"}).status_code == 200
    assert client.patch("/api/admin/users/NEW-04", json={"password": "short"}).status_code == 400


def test_admin_keeps_own_session_when_changing_own_password(client):
    _in(client, ADMIN)
    _make(client, "NEW-ADM", is_admin=True)
    client.post("/api/signout", json={})
    _in(client, ("NEW-ADM", GOOD_PW))
    assert client.patch("/api/admin/users/NEW-ADM", json={"password": "changed-long-pw"}).status_code == 200
    assert client.get("/api/admin/users").status_code == 200


def test_delete_user_ends_sessions_and_removes_their_views(client):
    _in(client, ADMIN)
    _make(client, "NEW-05")
    other = srv.app.test_client()
    other.post("/api/signin", json={"id": "NEW-05", "password": GOOD_PW})
    assert other.post("/api/views", json={"name": "v", "screen": "company"}).status_code == 201
    assert client.delete("/api/admin/users/NEW-05").status_code == 200
    assert other.post("/api/analyze/company", json={}).status_code == 401
    assert db_users.get_user("NEW-05") is None
    from retrieval import views
    assert views.list_views("NEW-05") == []
    assert client.delete("/api/admin/users/NEW-05").status_code == 404


def test_cannot_lock_everyone_out(client):
    _in(client, ADMIN)
    assert client.delete("/api/admin/users/ADM-01").status_code == 400                       # yourself
    assert client.patch("/api/admin/users/ADM-01", json={"is_admin": False}).status_code == 400   # last administrator
    _make(client, "NEW-ADM2", is_admin=True)
    assert client.patch("/api/admin/users/ADM-01", json={"is_admin": False}).status_code == 400    # still yourself
    assert client.patch("/api/admin/users/NEW-ADM2", json={"is_admin": False}).status_code == 200  # another admin, not the last
    assert client.patch("/api/admin/users/ADM-01", json={}).status_code == 400                 # nothing to change


def test_demoting_an_admin_ends_their_sessions(client):
    _in(client, ADMIN)
    _make(client, "NEW-ADM3", is_admin=True)
    other = srv.app.test_client()
    other.post("/api/signin", json={"id": "NEW-ADM3", "password": GOOD_PW})
    assert other.get("/api/admin/users").status_code == 200
    client.patch("/api/admin/users/NEW-ADM3", json={"is_admin": False})
    assert other.get("/api/admin/users").status_code == 401


def test_unlock(client):
    for _ in range(srv.MAX_ATTEMPTS):
        client.post("/api/signin", json={"id": "BLFD-01", "password": "wrong"})
    assert client.post("/api/signin", json={"id": "BLFD-01", "password": "blfd2024"}).status_code == 403
    _in(client, ADMIN)
    assert [u["locked"] for u in client.get("/api/admin/users").get_json()["users"] if u["username"] == "BLFD-01"] == [True]
    assert client.post("/api/admin/users/BLFD-01/unlock", json={}).status_code == 200
    other = srv.app.test_client()
    assert other.post("/api/signin", json={"id": "BLFD-01", "password": "blfd2024"}).status_code == 200
    assert client.post("/api/admin/users/NOBODY/unlock", json={}).status_code == 404


def test_admin_actions_are_audited_without_secrets(client):
    _in(client, ADMIN)
    _make(client, "NEW-06")
    client.patch("/api/admin/users/NEW-06", json={"password": "another-long-pw", "document_ids": ["BLFD", "HDYN"]})
    client.delete("/api/admin/users/NEW-06")
    ev = {e["event"]: e for e in srv._audit_recent(50) if e["event"].startswith("admin_user_")}
    assert {"admin_user_created", "admin_user_updated", "admin_user_deleted"} <= set(ev)
    assert ev["admin_user_updated"]["detail"]["changed"] == ["document_ids", "owners", "password"]
    assert "another-long-pw" not in str(srv._audit_recent(200)) and GOOD_PW not in str(srv._audit_recent(200))


def test_audit_view_filters_and_flags_repeat_offenders(client):
    _in(client, BLFD)
    for _ in range(3):
        client.post("/api/analyze/financials", json={"ticker": "MCLD"})
    client.post("/api/signout", json={})
    _in(client, ADMIN)
    d = client.get("/api/admin/audit?user=blfd-01&event=analyze_denied").get_json()
    assert d["entries"] and all(e["username"] == "BLFD-01" and e["event"] == "analyze_denied" for e in d["entries"])
    assert "analyze_denied" in d["events"]
    assert any(f["username"] == "BLFD-01" and f["denied"] >= 3 for f in d["flagged"])
    newest_first = client.get("/api/admin/audit").get_json()["entries"]
    assert [e["ts"] for e in newest_first] == sorted((e["ts"] for e in newest_first), reverse=True)
    assert client.get("/api/admin/audit?n=abc").status_code == 400
    assert len(client.get("/api/admin/audit?n=2").get_json()["entries"]) == 2
