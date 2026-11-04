"""
pull_request.py — Authenticated pull-request pipeline.

A PullRequestAgent wraps the existing Retriever and enforces that:
  1. The caller holds a valid session token.
  2. Results are scoped to documents owned by the authenticated user.

Usage
-----
    from retrieval.pull_request import PullRequestAgent
    from retrieval.auth import login

    token = login("alice", "alice123")
    agent = PullRequestAgent(retriever, all_docs)
    results = agent.pull(token, "python retrieval")
"""
from __future__ import annotations
from dataclasses import dataclass

from .auth import AuthError, validate_token
from .retriever import Retriever
from .types import Document, Query
from .db_users import get_user


@dataclass
class PullResult:
    username: str
    query: str
    documents: list[Document]

    def __repr__(self) -> str:
        ids = [d.id for d in self.documents]
        return f"PullResult(user={self.username!r}, query={self.query!r}, docs={ids})"


class PullRequestAgent:
    """
    Authenticated retrieval agent.

    Parameters
    ----------
    retriever:
        A configured :class:`~retrieval.retriever.Retriever` instance.
    all_documents:
        The full corpus the retriever works over.  Used to filter results
        down to only the documents the authenticated user owns.
    """

    def __init__(self, retriever: Retriever, all_documents: list[Document]):
        self._retriever = retriever
        self._corpus: dict[str, Document] = {d.id: d for d in all_documents}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pull(self, token: str, query: str | Query, limit: int = 10) -> PullResult:
        """
        Perform an authenticated pull request.

        Parameters
        ----------
        token:
            Session token returned by :func:`~retrieval.auth.login`.
        query:
            Free-text query string or a :class:`~retrieval.types.Query` object.
        limit:
            Maximum number of results to return.

        Returns
        -------
        PullResult
            Contains the authenticated username, original query text, and the
            ranked documents that belong to this user.

        Raises
        ------
        AuthError
            If *token* is missing, expired, or invalid.
        """
        username = validate_token(token)          # raises AuthError if bad

        user = get_user(username)
        if user is None:                          # should not happen after valid token
            raise AuthError(f"User record for '{username}' not found.")

        owned_ids: set[str] = set(user.document_ids)

        # Run the full retrieval pipeline
        query_obj = Query(query, limit=limit) if isinstance(query, str) else query
        all_results: list[Document] = self._retriever.retrieve(query_obj)

        # Keep only documents this user owns
        user_results = [d for d in all_results if d.id in owned_ids]

        return PullResult(
            username=username,
            query=query_obj.text,
            documents=user_results,
        )
