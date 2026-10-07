"""只评测论文发现的 Recall@5/10，不执行 Agent 或 V0.1 问答评测。"""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.models import NonEmpty
from app.papers.catalog import DEFAULT_FIXTURE, load_paper_catalog
from app.papers.retriever import PaperRetriever
from app.papers.store import DEFAULT_INDEX, ChromaPaperVectorStore
from app.retrieval.embedding import QwenEmbeddingModel

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERIES = ROOT / "data/paper_discovery/queries.json"


class DiscoveryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: NonEmpty
    query: NonEmpty
    relevant_paper_ids: list[NonEmpty] = Field(min_length=1)


def load_queries(path, catalog):
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported discovery query version")
    cases = [DiscoveryCase.model_validate(c) for c in data["cases"]]
    if len(cases) != 8 or len({c.case_id for c in cases}) != 8:
        raise ValueError("Expected eight unique discovery cases")
    for case in cases:
        if len(set(case.relevant_paper_ids)) != len(case.relevant_paper_ids):
            raise ValueError("Duplicate relevance IDs")
        for identity in case.relevant_paper_ids:
            catalog.resolve(identity)
    return cases


def evaluate_discovery(retriever, cases):
    results = []
    for case in cases:
        started = perf_counter()
        hits = retriever.search(case.query, top_k=10)
        ids = [h.paper.paper_id for h in hits]
        relevant = set(case.relevant_paper_ids)
        scores = {
            f"recall_at_{k}": len(set(ids[:k]) & relevant) / len(relevant)
            for k in (5, 10)
        }
        results.append(
            {
                **case.model_dump(),
                **scores,
                "latency_ms": (perf_counter() - started) * 1000,
                "retrieved": [
                    {
                        "paper_id": h.paper.paper_id,
                        "title": h.paper.title,
                        "rank": h.rank,
                        "score": h.score,
                    }
                    for h in hits
                ],
            }
        )
    return {
        "cases": results,
        "macro_recall_at_5": sum(r["recall_at_5"] for r in results) / len(results),
        "macro_recall_at_10": sum(r["recall_at_10"] for r in results) / len(results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/paper-discovery-evaluation.json",
    )
    args = parser.parse_args()
    catalog = load_paper_catalog(args.fixture)
    cases = load_queries(args.queries, catalog)
    store = ChromaPaperVectorStore(args.index_dir)
    store.validate_provenance(args.fixture, args.embedding_model)
    retriever = PaperRetriever(
        QwenEmbeddingModel(args.embedding_model, device=args.device), store
    )
    report = evaluate_discovery(retriever, cases)
    report.update(
        backend="real_embedding_chroma",
        embedding_model=args.embedding_model,
        collection=store.collection.name,
        fixture_sha256=hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        queries_sha256=hashlib.sha256(args.queries.read_bytes()).hexdigest(),
        annotation_status="development_candidate_labels_not_user_reviewed",
        limitations="Small curated discovery fixture; not V0.1 QA evaluation or a literature-wide benchmark.",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Recall@5={report['macro_recall_at_5']:.4f}; Recall@10={report['macro_recall_at_10']:.4f}; {args.output}"
    )


if __name__ == "__main__":
    main()
