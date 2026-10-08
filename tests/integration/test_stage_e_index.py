"""真实本地 Chroma + 固定向量验证研究索引，不加载 Embedding 模型。"""

import json
from pathlib import Path

import pytest

from app.evidence.corpus import load_paper_corpus
from app.research.corpus_validation import corpus_snapshot
from scripts.evaluate_cross_paper import real_retriever

ROOT = Path(__file__).resolve().parents[2]


class FixedEmbedding:
    def __init__(self):
        self.document_batches = 0

    def embed_documents(self, texts):
        self.document_batches += 1
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, query):
        return [1.0, 0.0]


def setup(tmp_path):
    pytest.importorskip("chromadb")
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    snapshot = corpus_snapshot(
        ROOT / "data/papers", corpus, ROOT / "data/research_papers"
    )
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text()) | {
        "index_dir": str(tmp_path),
        "collection": "stage-e-test",
    }
    return corpus, snapshot, config, FixedEmbedding()


def test_chroma_snapshot_round_trip_and_prequery_filter(tmp_path, monkeypatch):
    from app.retrieval import embedding as module

    def forbidden(*args, **kwargs):
        raise AssertionError("No real embedding initialization in this test")

    monkeypatch.setattr(module, "QwenEmbeddingModel", forbidden)
    corpus, snapshot, config, embedding = setup(tmp_path)
    retriever = real_retriever(config, corpus, snapshot, embedding=embedding)
    assert retriever.vector_store.collection.count() == 46
    prior = embedding.document_batches
    reopened = real_retriever(config, corpus, snapshot, embedding=embedding)
    assert embedding.document_batches == prior
    expected = {e.evidence_id: e for e in corpus.text_evidence}
    for paper in ["blip-2-2301.12597v1", "llava-2304.08485v1"]:
        hits = reopened.search_text("Flamingo distractor", [paper], 50)
        assert hits and all(h.evidence.paper_id == paper for h in hits)
        assert all(h.evidence == expected[h.evidence.evidence_id] for h in hits)


def test_chroma_rejects_stale_snapshot_and_rewritten_index_text(tmp_path, monkeypatch):
    corpus, snapshot, config, embedding = setup(tmp_path)
    retriever = real_retriever(config, corpus, snapshot, embedding=embedding)
    collection = retriever.vector_store.collection

    def forbidden(*args, **kwargs):
        raise AssertionError("The test must always supply fixed embeddings")

    # 禁止 SDK 从更新后的文字隐式生成默认向量，测试始终保持离线。
    monkeypatch.setattr(collection, "_embed_record_set", forbidden)
    binding = dict(collection.metadata)
    collection.modify(metadata=binding | {"corpus_snapshot_id": "0" * 64})
    with pytest.raises(ValueError, match="configuration or corpus"):
        real_retriever(config, corpus, snapshot, embedding=embedding)
    collection.modify(metadata=binding)
    collection.update(
        ids=[corpus.text_evidence[0].evidence_id],
        documents=["Untraceable injected fact"],
        embeddings=[[1.0, 0.0]],
    )
    with pytest.raises(ValueError, match="index_provenance"):
        real_retriever(config, corpus, snapshot, embedding=embedding)
