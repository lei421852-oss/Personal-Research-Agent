from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evidence.corpus import load_paper_corpus
from app.papers.catalog import DEFAULT_FIXTURE, PaperCatalog, load_paper_catalog
from app.papers.models import PaperDocument, RetrievedPaper
from app.papers.retriever import PaperRetriever
from app.papers.store import ChromaPaperVectorStore
from app.retrieval.errors import RetrievalDependencyError
from app.retrieval.vector_store import ChromaTextVectorStore
from eval.paper_discovery import DEFAULT_QUERIES, evaluate_discovery, load_queries

ROOT = Path(__file__).resolve().parents[3]


CLIP = "clip-2103.00020v1"


BLIP = "blip-2201.12086v2"


BLIP2 = "blip-2-2301.12597v1"


@pytest.fixture
def catalog():
    return load_paper_catalog()


@pytest.fixture
def corpus():
    return load_paper_corpus(ROOT / "data/papers")


class FakeEmbedding:
    def __init__(self):
        self.queries = []

    def embed_query(self, query):
        self.queries.append(query)
        return [1.0, 0.0]


class FixedPaperStore:
    def __init__(self, papers):
        self.hits = [
            RetrievedPaper(paper=p, score=1.0 - i * 0.1, rank=i + 1)
            for i, p in enumerate(papers)
        ]
        self.calls = []

    def search(self, vector, *, top_k):
        self.calls.append(top_k)
        return self.hits[:top_k]


@pytest.mark.parametrize(
    "update",
    [
        {"title": " "},
        {"abstract": ""},
        {"authors": []},
        {"authors": [" "]},
        {"year": True},
        {"year": 1800},
        {"venue": " "},
        {"keywords": [""]},
    ],
)
def test_document_validation(catalog, update):
    values = catalog.resolve(CLIP).model_dump() | update
    with pytest.raises(ValidationError):
        PaperDocument.model_validate(values)


def test_catalog_unique_sources_and_no_query_keywords(catalog):
    assert len(catalog.documents) == 25
    assert all(not p.keywords for p in catalog.documents.values())
    assert all(r.source_url.host == "arxiv.org" for r in catalog.records)
    assert {
        CLIP,
        BLIP,
        "flamingo-2204.14198v1",
        BLIP2,
        "frozen-2106.13884v1",
        "llava-2304.08485v1",
        "minigpt-4-2304.10592v1",
        "instructblip-2305.06500v1",
    } <= catalog.documents.keys()
    with pytest.raises(ValueError):
        PaperCatalog([catalog.records[0], catalog.records[0]])


@pytest.mark.parametrize(
    "query,k",
    [
        ("", 3),
        (" ", 3),
        (None, 3),
        ("query", 0),
        ("query", -1),
        ("query", True),
        ("query", 1.5),
    ],
)
def test_retriever_invalid_input_before_embedding(query, k):
    embedding = FakeEmbedding()
    retriever = PaperRetriever(embedding, FixedPaperStore([]))
    with pytest.raises(ValueError):
        retriever.search(query, k)
    assert not embedding.queries


def test_retriever_topk_dedup_and_contiguous_rank(catalog):
    embedding = FakeEmbedding()
    store = FixedPaperStore(
        [catalog.resolve(CLIP), catalog.resolve(CLIP), catalog.resolve(BLIP)]
    )
    hits = PaperRetriever(embedding, store).search(" query ", 20)
    assert [h.paper.paper_id for h in hits] == [CLIP, BLIP]
    assert [h.rank for h in hits] == [1, 2]
    assert embedding.queries == ["query"] and store.calls == [20]
    assert len(PaperRetriever(embedding, store).search("query", 1)) == 1


def test_real_chroma_roundtrip_idempotency_and_index_isolation(
    tmp_path, catalog, corpus
):
    pytest.importorskip("chromadb")
    papers = [catalog.resolve(CLIP), catalog.resolve(BLIP2)]
    store = ChromaPaperVectorStore(tmp_path, create=True)
    store.add(papers, [[1.0, 0.0], [0.0, 1.0]])
    store.add(papers, [[1.0, 0.0], [0.0, 1.0]])
    assert store.collection.count() == 2
    store.bind_provenance(DEFAULT_FIXTURE, "fake")
    reopened = ChromaPaperVectorStore(tmp_path)
    reopened.validate_provenance(DEFAULT_FIXTURE, "fake")
    hits = reopened.search([1.0, 0.0], top_k=20)
    assert hits[0].paper == papers[0]
    assert {h.paper.paper_id: h.paper for h in hits} == {p.paper_id: p for p in papers}
    evidence = ChromaTextVectorStore(tmp_path, "v01-evidence", create=True)
    evidence.add([corpus.text_evidence[0]], [[1.0, 0.0]])
    assert evidence.collection.count() == 1 and store.collection.count() == 2
    with pytest.raises(ValueError):
        reopened.validate_provenance(DEFAULT_FIXTURE, "different-model")
    with pytest.raises(ValueError):
        store.add([papers[0], papers[0]], [[1.0, 0.0], [1.0, 0.0]])


def test_empty_index_and_dependency_failure(tmp_path, catalog, monkeypatch):
    pytest.importorskip("chromadb")
    store = ChromaPaperVectorStore(tmp_path, create=True)
    assert store.search([1.0, 0.0], top_k=3) == []
    store.add([catalog.resolve(CLIP)], [[1.0, 0.0]])

    def fail(**kwargs):
        raise ConnectionError("private implementation error")

    monkeypatch.setattr(store.collection, "query", fail)
    with pytest.raises(RetrievalDependencyError):
        store.search([1.0, 0.0], top_k=3)


def test_discovery_eval_fixed_cases_and_recall(catalog):
    cases = load_queries(DEFAULT_QUERIES, catalog)
    assert len(cases) == 8

    class OracleForMetricTestOnly:
        def search(self, query, top_k):
            case = next(c for c in cases if c.query == query)
            return [
                RetrievedPaper(paper=catalog.resolve(i), score=1, rank=r + 1)
                for r, i in enumerate(case.relevant_paper_ids)
            ]

    report = evaluate_discovery(OracleForMetricTestOnly(), cases)
    assert report["macro_recall_at_5"] == report["macro_recall_at_10"] == 1
