from __future__ import annotations
from abc import ABC, abstractmethod
from .types import Document, Query


class Ranker(ABC):
    @abstractmethod
    def rank(self, query: Query, docs: list[Document]) -> list[Document]:
        """Return docs with `score` set, sorted best-first."""


class KeywordRanker(Ranker):
    """Baseline: fraction of query terms found in the document."""

    def rank(self, query: Query, docs: list[Document]) -> list[Document]:
        terms = set(query.text.lower().split())
        for d in docs:
            words = set(d.content.lower().split())
            d.score = len(terms & words) / len(terms) if terms else 0.0
        return sorted(docs, key=lambda d: d.score, reverse=True)
