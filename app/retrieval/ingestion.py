"""One ingestion path for every paper; fixed-source upserts are idempotent."""

from dataclasses import dataclass

from app.evidence.corpus import PaperCorpus
from app.retrieval.contracts import EmbeddingModel, TextVectorStore


@dataclass(frozen=True)
class IngestionResult:
    papers: int
    indexed_text: int
    available_figures: int  # Figures are located via corpus, not in this text index.


def ingest_text_corpus(
    corpus: PaperCorpus,
    embedding: EmbeddingModel,
    store: TextVectorStore,
    batch_size: int = 16,
) -> IngestionResult:
    if type(batch_size) is not int or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    for start in range(0, len(corpus.text_evidence), batch_size):
        batch = list(corpus.text_evidence[start : start + batch_size])
        vectors = embedding.embed_documents([item.text for item in batch])
        if len(vectors) != len(batch):
            raise ValueError("Embedding count differs from evidence count")
        store.add(batch, vectors)
    return IngestionResult(
        len(corpus.metadata), len(corpus.text_evidence), len(corpus.figure_evidence)
    )
