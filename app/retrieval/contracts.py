"""论文范围文本检索的轻量契约，不引入额外依赖。"""

from typing import Protocol

from app.evidence.models import RetrievedEvidence, TextEvidence


class EmbeddingModel(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, query: str) -> list[float]: ...


class TextVectorStore(Protocol):
    def add(
        self, evidence: list[TextEvidence], embeddings: list[list[float]]
    ) -> None: ...
    def search(
        self, query_embedding: list[float], *, paper_ids: list[str] | None, top_k: int
    ) -> list[RetrievedEvidence]: ...


class TextRetrieval(Protocol):
    def search_text(
        self, query: str, paper_ids: list[str] | None, top_k: int
    ) -> list[RetrievedEvidence]: ...
