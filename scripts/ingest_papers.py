"""显式建立独立论文元数据索引，不写入 V0.1 Evidence Index。"""

import argparse
from pathlib import Path

from app.papers.catalog import DEFAULT_FIXTURE, load_paper_catalog
from app.papers.store import DEFAULT_INDEX, ChromaPaperVectorStore
from app.retrieval.embedding import QwenEmbeddingModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    catalog = load_paper_catalog(args.fixture)
    store = ChromaPaperVectorStore(args.index_dir, create=True)
    prior = (store.collection.metadata or {}).get("embedding_model")
    if prior not in (None, args.embedding_model):
        parser.error("Embedding model differs; use a new paper index directory")
    embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
    papers = list(catalog.documents.values())
    store.add(papers, embedding.embed_documents([p.retrieval_text for p in papers]))
    if store.collection.count() != len(papers):
        raise ValueError("Index contains stale paper IDs; use a new directory")
    store.bind_provenance(args.fixture, args.embedding_model)
    print(
        f"Paper index ready: {len(papers)} unique papers; collection={store.collection.name}"
    )


if __name__ == "__main__":
    main()
