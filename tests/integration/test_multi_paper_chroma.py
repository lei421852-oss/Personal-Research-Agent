"""本地真实 Chroma 配合固定向量，不进行真实模型推理。"""

from pathlib import Path

import pytest

pytest.importorskip("chromadb")

from app.evidence.corpus import load_paper_corpus
from app.retrieval.ingestion import ingest_text_corpus
from app.retrieval.retriever import TextRetriever
from app.retrieval.vector_store import ChromaTextVectorStore

ROOT = Path(__file__).resolve().parents[2]


class FixedEmbedding:
    def __init__(self, corpus):
        keys = ["clip", "blip", "flamingo"]
        self.vectors = {
            e.text: [float(e.paper_id == corpus.paper_aliases[k]) for k in keys]
            for e in corpus.text_evidence
        }

    def embed_documents(self, texts):
        return [self.vectors[text] for text in texts]

    def embed_query(self, query):
        return [0.0, 1.0, 0.0]


@pytest.mark.parametrize(
    "papers", [["clip"], ["blip"], ["flamingo"], ["clip", "blip"], None]
)
def test_persistent_corpus_idempotent_ingestion_and_prequery_filter(tmp_path, papers):
    corpus = load_paper_corpus(ROOT / "data/papers")
    store = ChromaTextVectorStore(tmp_path, "multi-paper", create=True)
    embedding = FixedEmbedding(corpus)
    ingest_text_corpus(corpus, embedding, store)
    ingest_text_corpus(corpus, embedding, store)
    assert store.collection.count() == len(corpus.text_evidence)
    reopened = ChromaTextVectorStore(tmp_path, "multi-paper")
    hits = TextRetriever(
        embedding, reopened, paper_aliases=corpus.paper_aliases
    ).search_text("BLIP CapFilt noisy pairs", papers, 30)
    allowed = (
        {corpus.paper_aliases[p] for p in papers}
        if papers is not None
        else {m.paper_id for m in corpus.metadata}
    )
    assert {hit.evidence.paper_id for hit in hits} == allowed
    original = {e.evidence_id: e for e in corpus.text_evidence}
    assert all(hit.evidence == original[hit.evidence.evidence_id] for hit in hits)
    # 全库 Top-1 刻意接近 BLIP；同一查询限制为 CLIP 时必须返回 CLIP。
    retriever = TextRetriever(embedding, reopened, paper_aliases=corpus.paper_aliases)
    assert (
        retriever.search_text("BLIP", None, 1)[0].evidence.paper_id
        == corpus.paper_aliases["blip"]
    )
    assert (
        retriever.search_text("BLIP", ["clip"], 1)[0].evidence.paper_id
        == corpus.paper_aliases["clip"]
    )
