"""真实 Chroma、准备语料与固定向量组合测试，不调用模型。"""

from pathlib import Path

import pytest

pytest.importorskip("chromadb")

from app.evidence.corpus import load_paper_corpus
from app.retrieval.ingestion import ingest_text_corpus
from app.retrieval.retriever import TextRetriever
from app.retrieval.vector_store import ChromaTextVectorStore
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from scripts.demo_day3 import FixedVectorStore, FixtureEmbedding, run_demo

ROOT = Path(__file__).resolve().parents[2]


def test_manual_search_observation_then_read_page_observation():
    corpus = load_paper_corpus(ROOT / "data/papers")
    embedding, store = FixtureEmbedding(corpus), FixedVectorStore()
    ingest_text_corpus(corpus, embedding, store)
    report = run_demo(corpus, TextRetriever(embedding, store), "fixture")
    first, second = report["steps"]
    assert first["observation"]["payload"]["evidence"][0]["page"] == 9
    assert first["trace"]["result_count"] == 1
    assert second["trace"]["result_count"] == 2
    assert second["observation"]["payload"]["page"] == 11
    assert all(report["checks"].values())
    assert report["ranking_is_synthetic"] and not report["llm_connected"]
    assert not report["agent_loop"]


def test_registered_search_uses_real_chroma_paper_filter_and_page_context(tmp_path):
    corpus = load_paper_corpus(ROOT / "data/papers")
    embedding = FixtureEmbedding(corpus)
    store = ChromaTextVectorStore(tmp_path, "day3-paper-tools", create=True)
    ingest_text_corpus(corpus, embedding, store)
    executor = ToolExecutor(
        create_paper_registry(corpus, TextRetriever(embedding, store))
    )
    found = executor.execute(
        "search_text", {"query": "Perceiver", "paper_ids": ["clip"], "top_k": 1}
    )
    assert found.status == "success"
    assert found.payload["evidence"][0]["paper_id"] == corpus.paper_aliases["clip"]
    figure = executor.execute(
        "find_figure", {"paper_id": "flamingo", "figure_id": "Figure 3"}
    )
    assert figure.status == "success" and figure.payload["figure"]["page"] == 9
    page = executor.execute("read_page", {"paper_id": "flamingo", "page": 11})
    assert page.status == "success" and page.result_count == 2
    assert len(executor.traces) == 3
