from retrieval import Retriever, Document, Query
from retrieval.sources import InMemorySource, Source


class Boom(Source):
    name = "boom"
    def fetch(self, query):
        raise RuntimeError("down")


def docs():
    return [Document("1", "alpha beta", "m"), Document("2", "beta gamma", "m")]


def test_ranks_and_limits():
    r = Retriever([InMemorySource(docs())])
    out = r.retrieve(Query("alpha beta", limit=1))
    assert [d.id for d in out] == ["1"]


def test_failing_source_is_isolated():
    r = Retriever([Boom(), InMemorySource(docs())])
    assert len(r.retrieve("beta")) == 2


def test_dedupes_across_sources():
    r = Retriever([InMemorySource(docs()), InMemorySource(docs())])
    assert len(r.retrieve("beta")) == 2
