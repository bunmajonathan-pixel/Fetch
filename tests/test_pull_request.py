"""
test_pull_request.py — Tests for auth and the PullRequestAgent.
"""
import pytest

from retrieval import (
    Document, Retriever,
    AuthError, login, logout, validate_token,
    PullRequestAgent,
    register_user,
)
from retrieval.sources import InMemorySource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALL_DOCS = [
    Document("1", "python data retrieval framework", "mem"),
    Document("2", "cooking pasta at home", "mem"),
    Document("3", "building a retrieval pipeline in python", "mem"),
]


@pytest.fixture()
def retriever():
    return Retriever([InMemorySource(ALL_DOCS)])


@pytest.fixture()
def agent(retriever):
    return PullRequestAgent(retriever, ALL_DOCS)


# ---------------------------------------------------------------------------
# Auth — login / validate / logout
# ---------------------------------------------------------------------------

def test_login_valid_credentials():
    token = login("alice", "alice123")
    assert isinstance(token, str) and len(token) == 64


def test_login_wrong_password():
    with pytest.raises(AuthError):
        login("alice", "wrongpassword")


def test_login_unknown_user():
    with pytest.raises(AuthError):
        login("nobody", "anything")


def test_validate_token_returns_username():
    token = login("alice", "alice123")
    assert validate_token(token) == "alice"


def test_validate_token_bad_token():
    with pytest.raises(AuthError):
        validate_token("not-a-real-token")


def test_logout_invalidates_token():
    token = login("alice", "alice123")
    logout(token)
    with pytest.raises(AuthError):
        validate_token(token)


# ---------------------------------------------------------------------------
# PullRequestAgent — scoping and gating
# ---------------------------------------------------------------------------

def test_pull_returns_only_owned_docs(agent):
    """Alice owns docs 1 and 3; a query matching all docs must return only 1 & 3."""
    token = login("alice", "alice123")
    result = agent.pull(token, "python retrieval")
    ids = {d.id for d in result.documents}
    assert ids == {"1", "3"}


def test_pull_result_username(agent):
    token = login("bob", "bob456")
    result = agent.pull(token, "pasta")
    assert result.username == "bob"


def test_pull_scoped_to_user_bob(agent):
    """Bob owns only doc 2 (cooking pasta)."""
    token = login("bob", "bob456")
    result = agent.pull(token, "pasta")
    assert [d.id for d in result.documents] == ["2"]


def test_pull_rejects_invalid_token(agent):
    with pytest.raises(AuthError):
        agent.pull("bad-token", "python")


def test_pull_rejects_logged_out_token(agent):
    token = login("alice", "alice123")
    logout(token)
    with pytest.raises(AuthError):
        agent.pull(token, "python")


def test_pull_no_results_outside_ownership(agent):
    """Bob queries for 'python' — he gets nothing because he only owns doc 2."""
    token = login("bob", "bob456")
    result = agent.pull(token, "python retrieval")
    assert result.documents == []


def test_register_and_pull_new_user(agent):
    register_user("charlie", "charlie789", document_ids=["2", "3"])
    token = login("charlie", "charlie789")
    result = agent.pull(token, "python pasta")
    ids = {d.id for d in result.documents}
    assert ids == {"2", "3"}
