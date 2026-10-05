"""Adapted from Personal RAG: src/rag/vector_store.py.

Fresh, independent collections only; no implicit deletion or rebuilding.
"""

from pathlib import Path
from typing import Any

from app.evidence.models import RetrievedEvidence, TextEvidence


class ChromaTextVectorStore:
    def __init__(
        self,
        persist_directory: str | Path,
        collection_name: str,
        *,
        create: bool = False,
        client: Any = None,
    ):
        if client is None:
            import chromadb

            client = chromadb.PersistentClient(path=str(persist_directory))
        self.client = client
        if create:
            self.collection = client.get_or_create_collection(
                name=collection_name, configuration={"hnsw": {"space": "cosine"}}
            )
        else:
            self.collection = client.get_collection(name=collection_name)
        if self.collection.configuration.get("hnsw", {}).get("space") != "cosine":
            raise ValueError("Text evidence collection must use cosine distance")

    def add(self, evidence: list[TextEvidence], embeddings: list[list[float]]) -> None:
        if len(evidence) != len(embeddings):
            raise ValueError("evidence and embeddings must have the same length")
        if not evidence:
            return
        if any(not isinstance(item, TextEvidence) for item in evidence):
            raise ValueError("Text store accepts only TextEvidence")
        self.collection.upsert(
            ids=[item.evidence_id for item in evidence],
            embeddings=embeddings,
            documents=[item.text for item in evidence],
            metadatas=[item.model_dump(exclude={"text"}) for item in evidence],
        )

    def search(
        self, query_embedding: list[float], *, paper_ids: list[str] | None, top_k: int
    ) -> list[RetrievedEvidence]:
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be positive")
        if paper_ids == [] or self.collection.count() == 0:
            return []
        where = {"modality": "text"}
        if paper_ids is not None:
            where = {"$and": [{"modality": "text"}, {"paper_id": {"$in": paper_ids}}]}
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.collection.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for identity, text, metadata, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
            strict=True,
        ):
            if (
                text is None
                or metadata is None
                or identity != metadata.get("evidence_id")
            ):
                raise ValueError("Corrupt evidence record")
            hits.append(
                RetrievedEvidence(
                    evidence=TextEvidence(**metadata, text=text),
                    retrieval_score=1.0 - float(distance),
                )
            )
        return hits
