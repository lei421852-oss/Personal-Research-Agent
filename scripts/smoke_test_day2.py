"""显式运行八个真实检索冒烟问题，不生成答案或调用 VLM。"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.evidence.corpus import load_paper_corpus
from app.evidence.smoke import load_smoke_questions

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers-dir", type=Path, default=ROOT / "data/papers")
    parser.add_argument(
        "--questions", type=Path, default=ROOT / "data/smoke/day2_queries.json"
    )
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/chroma/day2")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.top_k <= 0:
        parser.error("--top-k must be positive")
    corpus = load_paper_corpus(args.papers_dir)
    questions = load_smoke_questions(args.questions, corpus)
    from app.retrieval.embedding import QwenEmbeddingModel
    from app.retrieval.ingestion import ingest_text_corpus
    from app.retrieval.retriever import TextRetriever
    from app.retrieval.vector_store import ChromaTextVectorStore

    embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
    store = ChromaTextVectorStore(args.index_dir, "papers-day2-text", create=True)
    ingest_text_corpus(corpus, embedding, store)
    retriever = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
    records = []
    for question in questions:
        print("\nQuestion:", question.question)
        requests = []
        for request in question.requests:
            results = retriever.search_text(
                request.query, [request.paper_key], args.top_k
            )
            canonical = corpus.paper_aliases[request.paper_key]
            if any(hit.evidence.paper_id != canonical for hit in results):
                raise RuntimeError("Paper filter leaked evidence from another paper")
            hits = []
            print("Paper scope:", request.paper_key)
            for hit in results:
                evidence = hit.evidence
                location = corpus.locate(evidence.evidence_id)
                print(
                    f"{evidence.paper_id} page={evidence.page} id={evidence.evidence_id} "
                    f"score={hit.retrieval_score:.4f}\n{evidence.text[:350]}"
                )
                hits.append(
                    hit.model_dump()
                    | {
                        "source_pdf": str(location.pdf_path),
                        "source_page_file": str(location.page_text_path),
                    }
                )
            requests.append(
                request.model_dump()
                | {"results": hits, "manual_relevance_review": "pending"}
            )
        records.append(
            {
                "question_id": question.question_id,
                "question": question.question,
                "requests": requests,
            }
        )
    report = {
        "status": "requires_manual_review",
        "note": "Retrieval-only smoke, no generated cross-paper answers.",
        "embedding_model": args.embedding_model,
        "device": args.device,
        "top_k": args.top_k,
        "questions": records,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d-%H%M%S-%f")
    path = args.output_dir / f"{stamp}-多模态论文研究智能体-Day2真实检索冒烟测试.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Saved for manual review:", path)


if __name__ == "__main__":
    main()
