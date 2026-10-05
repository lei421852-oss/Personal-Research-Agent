"""Inspect prepared papers by default; --execute explicitly runs real embedding."""

import argparse
from dataclasses import asdict
from pathlib import Path

from app.evidence.corpus import load_paper_corpus

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers-dir", type=Path, default=ROOT / "data/papers")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/chroma/day2")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    corpus = load_paper_corpus(args.papers_dir)
    for meta in corpus.metadata:
        print(
            meta.paper_key,
            meta.paper_id,
            "text:",
            sum(e.paper_id == meta.paper_id for e in corpus.text_evidence),
            "figures:",
            sum(e.paper_id == meta.paper_id for e in corpus.figure_evidence),
        )
    if not args.execute:
        print(
            "Validated corpus only; no embedding or index writes. Add --execute to ingest with a real model."
        )
        return
    from app.retrieval.embedding import QwenEmbeddingModel
    from app.retrieval.ingestion import ingest_text_corpus
    from app.retrieval.vector_store import ChromaTextVectorStore

    store = ChromaTextVectorStore(args.index_dir, "papers-day2-text", create=True)
    embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
    print(asdict(ingest_text_corpus(corpus, embedding, store)))


if __name__ == "__main__":
    main()
