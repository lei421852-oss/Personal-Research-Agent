"""论文级检索接口；复用 Embedding 契约，不依赖 Agent。"""

from typing import Protocol

from app.papers.models import RetrievedPaper
from app.retrieval.contracts import EmbeddingModel
from app.retrieval.errors import RetrievalDependencyError


class PaperVectorStore(Protocol):
    def search(
        self, query_embedding: list[float], *, top_k: int
    ) -> list[RetrievedPaper]: ...


class PaperRetriever:
    def __init__(self, embedding_model: EmbeddingModel, vector_store: PaperVectorStore):
        self.embedding_model, self.vector_store = embedding_model, vector_store

    def search(self, query: str, top_k: int = 20) -> list[RetrievedPaper]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be nonempty")
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            vector = self.embedding_model.embed_query(query.strip())
        except Exception as exc:
            raise RetrievalDependencyError("Paper query embedding failed") from exc
        hits = self.vector_store.search(vector, top_k=top_k)
        unique = {}
        for hit in hits:
            if not isinstance(hit, RetrievedPaper):
                raise TypeError("Invalid paper search result")
            unique.setdefault(hit.paper.paper_id, hit)
        # 保留首个返回结果，去重后重新连续编号；不添加重排模型。
        return [
            hit.model_copy(update={"rank": rank})
            for rank, hit in enumerate(list(unique.values())[:top_k], 1)
        ]
