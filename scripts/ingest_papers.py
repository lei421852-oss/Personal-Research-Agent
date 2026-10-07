"""显式建立独立论文元数据索引，不写入 V0.1 Evidence Index。"""

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path

from app.papers.catalog import DEFAULT_FIXTURE, load_paper_catalog
from app.papers.models import REPRESENTATION_VERSION
from app.papers.store import DEFAULT_INDEX, ChromaPaperVectorStore
from app.retrieval.embedding import QwenEmbeddingModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--enrich", action="store_true", help="离线抽取论文画像；不改变原始 fixture"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--profile-cache", type=Path, default=Path("artifacts/paper-profiles.json")
    )
    parser.add_argument("--report", type=Path, help="保存画像、抽取成本和实际索引文本")
    args = parser.parse_args()
    catalog = load_paper_catalog(args.fixture)
    store = ChromaPaperVectorStore(args.index_dir, create=True)
    prior = (store.collection.metadata or {}).get("embedding_model")
    if prior not in (None, args.embedding_model):
        parser.error("Embedding model differs; use a new paper index directory")
    papers = list(catalog.documents.values())
    enrichment = None
    if args.enrich:
        from dotenv import load_dotenv

        from app.llm.provider import OpenAICompatibleLLMClient
        from app.papers.profiles import PaperProfileExtractor, enrich_papers

        if args.env_file.is_file():
            load_dotenv(args.env_file, override=False)
        if any(
            not os.environ.get(f"LLM_{field}")
            for field in ("API_KEY", "MODEL", "BASE_URL")
        ):
            parser.error("Offline enrichment requires configured LLM credentials")
        with ExitStack() as stack:
            llm = OpenAICompatibleLLMClient(
                api_key=os.environ["LLM_API_KEY"],
                model=os.environ["LLM_MODEL"],
                base_url=os.environ["LLM_BASE_URL"],
            )
            stack.callback(llm.close)
            papers, enrichment = enrich_papers(
                papers, PaperProfileExtractor(llm), cache_path=args.profile_cache
            )
    # 所有画像准备完成后再生成向量，单次 upsert 保持同一表示版本。
    embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
    store.add(papers, embedding.embed_documents([p.retrieval_text for p in papers]))
    if store.collection.count() != len(papers):
        raise ValueError("Index contains stale paper IDs; use a new directory")
    store.bind_provenance(args.fixture, args.embedding_model)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "representation_version": REPRESENTATION_VERSION,
                    "embedding_model": args.embedding_model,
                    "enrichment": enrichment,
                    "papers": [
                        {
                            "document": p.model_dump(mode="json"),
                            "embedding_text": p.retrieval_text,
                        }
                        for p in papers
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    print(
        f"Paper index ready: {len(papers)} unique papers; collection={store.collection.name}"
    )


if __name__ == "__main__":
    main()
