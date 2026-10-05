"""Adapted from Personal RAG: src/rag/retriever.py."""

from app.evidence.models import RetrievedEvidence
from app.retrieval.contracts import EmbeddingModel, TextVectorStore


class TextRetriever:
    def __init__(self, embedding_model: EmbeddingModel, vector_store: TextVectorStore):
        self.embedding_model = embedding_model
        self.vector_store = vector_store

    def search_text(
        self, query: str, paper_ids: list[str] | None = None, top_k: int = 3
    ) -> list[RetrievedEvidence]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be nonempty")
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        if paper_ids is not None:
            if not isinstance(paper_ids, list) or any(
                not isinstance(p, str) or not p.strip() for p in paper_ids
            ):
                raise ValueError("paper_ids must contain nonempty strings")
            if not paper_ids:
                return []  # Empty selection searches no papers, never the full corpus.
        vector = self.embedding_model.embed_query(query.strip())
        return self.vector_store.search(vector, paper_ids=paper_ids, top_k=top_k)
