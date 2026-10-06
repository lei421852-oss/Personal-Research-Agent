"""需显式运行的真实模型检查；单元测试和实现过程不会执行。

在仓库根目录运行：python -m scripts.smoke_test_day1 --mode text。
输出供人工核查，不能自动宣称 Day 1 验收通过。
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(
    "/mnt/d/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体"
)


def run_text(args, metadata):
    from app.evidence.corpus import load_text_corpus
    from app.retrieval.embedding import QwenEmbeddingModel
    from app.retrieval.retriever import TextRetriever
    from app.retrieval.vector_store import ChromaTextVectorStore

    evidence = load_text_corpus(args.paper_dir)
    if not evidence:
        raise ValueError("No prepared text evidence")
    embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
    store = ChromaTextVectorStore(args.index_dir, "blip-day1-text", create=True)
    # 幂等 upsert，不删除集合；语料、模型标识或向量维度改变后，
    # 使用新的索引目录，避免复用不兼容索引。
    store.add(evidence, embedding.embed_documents([item.text for item in evidence]))
    retriever = TextRetriever(embedding, store)
    reports = []
    for case in metadata["smoke_queries"]:
        hits = retriever.search_text(case["query"], [metadata["paper_id"]], args.top_k)
        print("\nQuery:", case["query"])
        for hit in hits:
            item = hit.evidence
            print(
                f"{item.paper_id} page={item.page} id={item.evidence_id} "
                f"score={hit.retrieval_score:.4f}\n{item.text[:350]}"
            )
        reports.append(
            {
                "query": case["query"],
                "expected_pages": case["expected_pages"],
                "expected_terms": case["expected_terms"],
                "results": [item.model_dump() for item in hits],
                "manual_relevance_review": "pending",
            }
        )
    return {
        "indexed_evidence": len(evidence),
        "queries": reports,
        "embedding_model": args.embedding_model,
        "device": args.device,
    }


def run_figure(args, metadata):
    from app.evidence.models import FigureEvidence, make_evidence_id
    from app.vision.analysis import FigureAnalyzer
    from app.vision.provider import OpenAICompatibleVisionClient

    # 只用环境配置，不读取其他项目的凭据。
    api_key = os.environ.get("VLM_API_KEY")
    model = os.environ.get("VLM_MODEL")
    base_url = os.environ.get("VLM_BASE_URL")
    if not all([api_key, model, base_url]):
        raise ValueError(
            "Set VLM_API_KEY, VLM_MODEL and VLM_BASE_URL for a vision-capable provider"
        )
    entry = metadata["figures"][0]
    figure = FigureEvidence(
        evidence_id=make_evidence_id(
            metadata["paper_id"], entry["page"], "figure", entry["figure_id"]
        ),
        paper_id=metadata["paper_id"],
        page=entry["page"],
        figure_id=entry["figure_id"],
        text=entry["related_text"],
        caption=entry["caption"],
        asset_path=str((args.paper_dir / entry["asset_path"]).resolve()),
    )
    client = OpenAICompatibleVisionClient(
        api_key=api_key, model=model, base_url=base_url
    )
    try:
        result = FigureAnalyzer(client).analyze_figure(figure, args.question)
    finally:
        client.close()
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return {
        "model": model,
        "figure": figure.model_dump(),
        "question": args.question,
        "analysis": result.model_dump(),
        "manual_grounding_review": "pending",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["text", "figure", "both"], required=True)
    parser.add_argument("--paper-dir", type=Path, default=ROOT / "data/papers/blip")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/chroma/day1")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--question", default="Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。"
    )
    args = parser.parse_args()
    if args.top_k <= 0:
        parser.error("--top-k must be positive")
    metadata = json.loads(
        (args.paper_dir / "metadata.json").read_text(encoding="utf-8")
    )
    report = {
        "status": "requires_manual_review",
        "paper_id": metadata["paper_id"],
        "note": "This is a three-query smoke test, not a statistical benchmark.",
    }
    if args.mode in ("text", "both"):
        report["text"] = run_text(args, metadata)
    if args.mode in ("figure", "both"):
        report["figure"] = run_figure(args, metadata)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d-%H%M%S-%f")
    output = (
        args.output_dir / f"{stamp}-多模态论文研究智能体-{args.mode}-真实冒烟测试.json"
    )
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\nSaved for manual review:", output)
    print(
        "Inspect relevance, visual observations, context support and limitations before declaring Day 1 Passed."
    )


if __name__ == "__main__":
    main()
