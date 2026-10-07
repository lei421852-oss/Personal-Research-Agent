"""独立论文集合的 Chroma 适配器；只复用连接和 cosine 配置。"""

import hashlib
from pathlib import Path

from app.papers.models import REPRESENTATION_VERSION, PaperDocument, RetrievedPaper
from app.retrieval.errors import RetrievalDependencyError
from app.retrieval.vector_store import ChromaTextVectorStore

COLLECTION = "papers-v02-metadata"
DEFAULT_INDEX = Path(__file__).resolve().parents[2] / "data/chroma/paper-discovery"


class ChromaPaperVectorStore:
    def __init__(
        self,
        persist_directory: str | Path = DEFAULT_INDEX,
        *,
        create=False,
        client=None,
    ):
        # 复用已有连接逻辑，但不调用 TextEvidence 的 add/search，不混用集合。
        connection = ChromaTextVectorStore(
            persist_directory, COLLECTION, create=create, client=client
        )
        self.collection = connection.collection

    def add(self, papers: list[PaperDocument], embeddings: list[list[float]]) -> None:
        if len(papers) != len(embeddings):
            raise ValueError("papers and embeddings must have the same length")
        if len({p.paper_id for p in papers}) != len(papers):
            raise ValueError("Duplicate paper_id in upsert batch")
        if not papers:
            return
        try:
            self.collection.upsert(
                ids=[p.paper_id for p in papers],
                embeddings=embeddings,
                documents=[p.retrieval_text for p in papers],
                metadatas=[
                    {
                        "record_type": "paper",
                        "paper_id": p.paper_id,
                        "paper_json": p.model_dump_json(),
                    }
                    for p in papers
                ],
            )
        except Exception as exc:
            raise RetrievalDependencyError("Paper index upsert failed") from exc

    def bind_provenance(self, fixture: Path, embedding_model: str) -> None:
        """校验模型身份；fixture hash 记录本轮来源，不静默混用其他模型。"""
        metadata = self.collection.metadata or {}
        if metadata.get("embedding_model") not in (None, embedding_model):
            raise ValueError(
                "Paper index embedding model mismatch; use a new index directory"
            )
        self.collection.modify(
            metadata=metadata
            | {
                "embedding_model": embedding_model,
                "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "representation_version": REPRESENTATION_VERSION,
            }
        )

    def validate_provenance(self, fixture: Path, embedding_model: str) -> None:
        metadata = self.collection.metadata or {}
        if (
            metadata.get("embedding_model") != embedding_model
            or metadata.get("fixture_sha256")
            != hashlib.sha256(fixture.read_bytes()).hexdigest()
        ):
            raise ValueError(
                "Paper index provenance mismatch; run ingestion with matching fixture/model"
            )

    def search(
        self, query_embedding: list[float], *, top_k: int
    ) -> list[RetrievedPaper]:
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            count = self.collection.count()
            if not count:
                return []
            result = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, count),
                where={"record_type": "paper"},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise RetrievalDependencyError("Paper index query failed") from exc
        hits = []
        for identity, text, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
            strict=True,
        ):
            if not metadata or identity != metadata.get("paper_id"):
                raise ValueError("Corrupt paper index identity")
            paper = PaperDocument.model_validate_json(metadata["paper_json"])
            # 老索引仍可只读评测；含画像的新记录必须与增强序列化完全一致。
            valid_texts = {paper.retrieval_text}
            if not paper.profile.has_terms:
                valid_texts.add(paper.legacy_retrieval_text)
            if paper.paper_id != identity or text not in valid_texts:
                raise ValueError("Corrupt paper index metadata")
            hits.append(
                RetrievedPaper(
                    paper=paper, score=1 - float(distance), rank=len(hits) + 1
                )
            )
        return hits
