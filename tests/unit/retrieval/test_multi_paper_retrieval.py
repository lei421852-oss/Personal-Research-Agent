from pathlib import Path

import pytest

from app.evidence.corpus import load_paper_corpus
from app.evidence.models import RetrievedEvidence
from app.retrieval.ingestion import ingest_text_corpus
from app.retrieval.retriever import TextRetriever

ROOT = Path(__file__).resolve().parents[3]


class FakeEmbedding:
    """Fixed vectors exercise contracts only, not real semantic retrieval."""

    def __init__(self, corpus):
        keys = ["clip", "blip", "flamingo"]
        self.vectors = {
            e.text: [float(e.paper_id == corpus.paper_aliases[k]) for k in keys]
            for e in corpus.text_evidence
        }

    def embed_documents(self, texts):
        return [self.vectors[text] for text in texts]

    def embed_query(self, query):
        return [0.0, 1.0, 0.0]  # Deliberately BLIP-like for all filter checks.


class FixedVectorStore:
    def __init__(self):
        self.records = {}

    def add(self, evidence, embeddings):
        for item, vector in zip(evidence, embeddings, strict=True):
            self.records[item.evidence_id] = item, vector

    def search(self, query_embedding, *, paper_ids, top_k):
        candidates = [
            (item, vector)
            for item, vector in self.records.values()
            if paper_ids is None or item.paper_id in paper_ids
        ]
        scored = [
            RetrievedEvidence(
                evidence=item,
                retrieval_score=sum(
                    a * b for a, b in zip(vector, query_embedding, strict=True)
                ),
            )
            for item, vector in candidates
        ]
        return sorted(scored, key=lambda hit: hit.retrieval_score, reverse=True)[:top_k]


@pytest.mark.parametrize(
    "papers", [["clip"], ["blip"], ["flamingo"], ["clip", "blip"], None]
)
def test_paper_filters_and_blip_query_cannot_escape_clip_scope(papers):
    corpus = load_paper_corpus(ROOT / "data/papers")
    store, embedding = FixedVectorStore(), FakeEmbedding(corpus)
    first = ingest_text_corpus(corpus, embedding, store, batch_size=4)
    ingest_text_corpus(corpus, embedding, store, batch_size=4)
    assert len(store.records) == len(corpus.text_evidence)
    assert first.indexed_text == 25 and first.available_figures == 3
    hits = TextRetriever(
        embedding, store, paper_aliases=corpus.paper_aliases
    ).search_text("How does BLIP CapFilt remove noisy image-text pairs?", papers, 30)
    allowed = (
        {corpus.paper_aliases[p] for p in papers}
        if papers is not None
        else {m.paper_id for m in corpus.metadata}
    )
    assert hits
    assert {hit.evidence.paper_id for hit in hits} == allowed
    assert all(
        corpus.locate(hit.evidence.evidence_id).page == hit.evidence.page
        for hit in hits
    )


def test_aliases_are_resolved_before_store_query():
    corpus = load_paper_corpus(ROOT / "data/papers")

    class CapturingStore(FixedVectorStore):
        def search(self, query_embedding, *, paper_ids, top_k):
            self.paper_ids = paper_ids
            return super().search(query_embedding, paper_ids=paper_ids, top_k=top_k)

    store = CapturingStore()
    retriever = TextRetriever(
        FakeEmbedding(corpus), store, paper_aliases=corpus.paper_aliases
    )
    retriever.search_text("BLIP", ["clip", "blip"], 3)
    assert store.paper_ids == [
        corpus.paper_aliases["clip"],
        corpus.paper_aliases["blip"],
    ]
