"""
demo.py — Demonstrates the authenticated pull-request pipeline.

Run from anywhere:
    python examples/demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # project root, so `retrieval` imports

from retrieval import (
    Document, Retriever, TTLCache,
    login, logout, AuthError,
    PullRequestAgent,
)
from retrieval.sources import InMemorySource

# ---------------------------------------------------------------------------
# Corpus — all documents in the system
# ---------------------------------------------------------------------------
ALL_DOCS = [
    Document("1", "python data retrieval framework", "mem"),
    Document("2", "cooking pasta at home", "mem"),
    Document("3", "building a retrieval pipeline in python", "mem"),
]

retriever = Retriever([InMemorySource(ALL_DOCS)], cache=TTLCache(ttl=60))
agent = PullRequestAgent(retriever, ALL_DOCS)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
DIVIDER = "-" * 55


def run_pull(username: str, password: str, query: str) -> None:
    print(f"\n{'='*55}")
    print(f"  User : {username}")
    print(f"  Query: {query!r}")
    print(DIVIDER)
    try:
        token = login(username, password)
        result = agent.pull(token, query)
        if result.documents:
            for d in result.documents:
                print(f"  [{d.score:.2f}] doc-{d.id}  {d.content}")
        else:
            print("  (no matching documents for this user)")
        logout(token)
    except AuthError as exc:
        print(f"  AUTH DENIED: {exc}")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
# Alice owns docs 1 & 3 (both about Python retrieval)
run_pull("alice", "alice123", "python retrieval")

# Bob owns doc 2 (cooking pasta)
run_pull("bob", "bob456", "pasta")

# Bob queries for python — he owns no Python docs, so gets nothing
run_pull("bob", "bob456", "python retrieval")

# Bad password
run_pull("alice", "wrongpassword", "python")

# Unknown user
run_pull("hacker", "letmein", "python")

print(f"\n{'='*55}")
print("  Demo complete.")
print(f"{'='*55}\n")
