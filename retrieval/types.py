from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Query:
    text: str
    limit: int = 10
    filters: dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> str:
        return f"{self.text}|{self.limit}|{sorted(self.filters.items())}"


@dataclass
class Document:
    id: str
    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
