"""扩充语料沿用原有 Tool/检索契约，原三篇快照和元数据边界不变。"""

from pathlib import Path

import pytest

from app.evidence.corpus import load_paper_corpus
from app.evidence.models import RetrievedEvidence
from app.evidence.resolver import EvidenceResolver
from app.papers.catalog import load_paper_catalog
from app.papers.evidence_access import DiscoveryEvidenceRepository
from app.research.corpus_validation import validate_index_records
from app.retrieval.ingestion import ingest_text_corpus
from app.retrieval.retriever import TextRetriever
from app.tools.discovery import DiscoveryScopedTextTool
from app.tools.executor import ToolExecutor
from app.tools.paper import ReadPageTool
from app.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def corpus():
    return load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )


class Embedding:
    def embed_documents(self, texts):
        return [[1.0] for _ in texts]

    def embed_query(self, query):
        return [1.0]


class Store:
    def __init__(self):
        self.records = {}

    def add(self, evidence, vectors):
        self.records.update({e.evidence_id: e for e in evidence})

    def search(self, vector, *, paper_ids, top_k):
        return [
            RetrievedEvidence(evidence=e, retrieval_score=1)
            for e in self.records.values()
            if paper_ids is None or e.paper_id in paper_ids
        ][:top_k]


def test_idempotent_index_rebuild_and_stale_record_rejection(corpus):
    store = Store()
    for _ in range(2):
        ingest_text_corpus(corpus, Embedding(), store)
    assert len(store.records) == 46
    validate_index_records(corpus, store.records.values())
    values = list(store.records.values())
    with pytest.raises(ValueError, match="index_provenance"):
        validate_index_records(corpus, values[1:])
    with pytest.raises(ValueError):
        validate_index_records(corpus, values + [values[0]])
    with pytest.raises(ValueError):
        validate_index_records(
            corpus, [values[0].model_copy(update={"text": "Changed text"}), *values[1:]]
        )


@pytest.mark.parametrize(
    "scope",
    [
        ["blip-2-2301.12597v1"],
        ["llava-2304.08485v1"],
        ["blip-2-2301.12597v1", "llava-2304.08485v1"],
    ],
)
def test_cross_paper_filter_applied_before_top_k(corpus, scope):
    store = Store()
    ingest_text_corpus(corpus, Embedding(), store)
    retriever = TextRetriever(Embedding(), store, paper_aliases=corpus.paper_aliases)
    hits = retriever.search_text("Flamingo evidence distractor", scope, 50)
    assert {h.evidence.paper_id for h in hits} == set(scope)
    assert all(h.evidence.paper_id in scope for h in hits)


def test_original_flamingo_ids_content_and_figures_unchanged(corpus):
    before = load_paper_corpus(ROOT / "data/papers")
    assert len(before.text_evidence) == 25 and len(before.figure_evidence) == 3
    resolver = EvidenceResolver(corpus)
    for evidence in before.text_evidence + before.figure_evidence:
        assert resolver.resolve(evidence.evidence_id) == evidence


def test_profile_cannot_be_used_as_prepared_evidence(corpus):
    catalog = load_paper_catalog()
    resolver = EvidenceResolver(corpus, paper_catalog=catalog)
    with pytest.raises(ValueError):
        resolver.resolve("ofa-2202.03052v1")
    assert catalog.resolve("ofa-2202.03052v1").abstract


def test_missing_paper_text_and_page_are_empty_not_fabricated(corpus):
    catalog = load_paper_catalog()
    papers = DiscoveryEvidenceRepository(corpus, catalog)
    registry = ToolRegistry()
    registry.register(ReadPageTool(papers))
    registry.register(
        DiscoveryScopedTextTool(TextRetriever(Embedding(), Store()), papers, catalog)
    )
    executor = ToolExecutor(registry)
    for name, args in [
        (
            "search_text",
            {"query": "OFA architecture", "paper_ids": ["ofa-2202.03052v1"]},
        ),
        ("read_page", {"paper_id": "ofa-2202.03052v1", "page": 1}),
        ("read_page", {"paper_id": "llava-2304.08485v1", "page": 99}),
    ]:
        obs = executor.execute(name, args)
        assert obs.status == "empty" and obs.result_count == 0


def test_duplicate_paper_roots_are_rejected():
    with pytest.raises(ValueError, match="Duplicate paper"):
        load_paper_corpus(
            ROOT / "data/papers", additional_papers_dir=ROOT / "data/papers"
        )


def test_new_research_paper_cannot_load_without_manifest(tmp_path):
    import shutil

    shutil.copytree(ROOT / "data/research_papers/llava", tmp_path / "llava")
    (tmp_path / "llava/source_manifest.json").unlink()
    (tmp_path / "llava/prepared_evidence.jsonl").unlink()
    with pytest.raises(ValueError, match="complete source provenance"):
        load_paper_corpus(ROOT / "data/papers", additional_papers_dir=tmp_path)
