from __future__ import annotations
from abc import ABC, abstractmethod
from ..types import Document, Query


class Source(ABC):
    """A backend that can return candidate documents for a query."""

    name: str = "source"

    @abstractmethod
    def fetch(self, query: Query) -> list[Document]:
        """Return candidate documents. Raise on failure; the Retriever handles it."""

    def close(self) -> None:
        """Release resources (connections, file handles)."""
