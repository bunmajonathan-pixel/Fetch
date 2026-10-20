from __future__ import annotations
from ..types import Document, Query
from .base import Source


class InMemorySource(Source):
    """Simple source over a list of documents. Useful for tests and demos."""

    def __init__(self, documents: list[Document], name: str = "memory"):
        self.name = name
        self._docs = documents

    def fetch(self, query: Query) -> list[Document]:
        terms = query.text.lower().split()
        return [d for d in self._docs if any(t in d.content.lower() for t in terms)]
