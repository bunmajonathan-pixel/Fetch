from __future__ import annotations
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .cache import TTLCache
from .ranking import KeywordRanker, Ranker
from .sources.base import Source
from .types import Document, Query

log = logging.getLogger(__name__)


class Retriever:
    """Pipeline: cache -> fan-out to sources -> dedupe -> rank -> truncate."""

    def __init__(self, sources: Sequence[Source], ranker: Ranker | None = None,
                 cache: TTLCache | None = None, max_workers: int = 8):
        self.sources = list(sources)
        self.ranker = ranker or KeywordRanker()
        self.cache = cache
        self.max_workers = max_workers

    def retrieve(self, query: Query | str) -> list[Document]:
        if isinstance(query, str):
            query = Query(query)

        if self.cache and (hit := self.cache.get(query.cache_key())) is not None:
            return hit

        candidates = self._fan_out(query)
        results = self.ranker.rank(query, self._dedupe(candidates))[: query.limit]

        if self.cache:
            self.cache.set(query.cache_key(), results)
        return results

    def _fan_out(self, query: Query) -> list[Document]:
        docs: list[Document] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(s.fetch, query): s for s in self.sources}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    docs.extend(fut.result())
                except Exception:  # one bad source shouldn't sink the query
                    log.exception("source %r failed", src.name)
        return docs

    @staticmethod
    def _dedupe(docs: list[Document]) -> list[Document]:
        seen: dict[str, Document] = {}
        for d in docs:
            seen.setdefault(d.id, d)
        return list(seen.values())

    def close(self) -> None:
        for s in self.sources:
            s.close()
