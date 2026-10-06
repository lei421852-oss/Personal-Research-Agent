"""人工安排的两次工具调用，不接 LLM，也没有 Agent 循环。

默认 fixture 模式用固定向量排序真实语料段落，只展示 Observation 契约，
不代表真实语义检索效果。--backend real 才使用 Day 2 索引与 Qwen 模型。
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.evidence.corpus import load_paper_corpus
from app.evidence.models import RetrievedEvidence
from app.retrieval.ingestion import ingest_text_corpus
from app.retrieval.retriever import TextRetriever
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts"


class FixtureEmbedding:
    """按固定规则选择真实第 9 页段落，不以语义质量作为选择依据。"""

    def __init__(self, corpus):
        paper_id = corpus.paper_aliases["flamingo"]
        self.vectors = {
            e.text: [1.0, 0.0]
            if e.paper_id == paper_id
            and e.page == 9
            and "Perceiver Resampler" in e.text
            else [0.0, 1.0]
            for e in corpus.text_evidence
        }

    def embed_documents(self, texts):
        return [self.vectors[t] for t in texts]

    def embed_query(self, query):
        return [1.0, 0.0]


class FixedVectorStore:
    def __init__(self):
        self.records = {}

    def add(self, evidence, embeddings):
        for e, vector in zip(evidence, embeddings, strict=True):
            self.records[e.evidence_id] = e, vector

    def search(self, query_embedding, *, paper_ids, top_k):
        candidates = [
            RetrievedEvidence(
                evidence=e,
                retrieval_score=sum(
                    a * b for a, b in zip(vector, query_embedding, strict=True)
                ),
            )
            for e, vector in self.records.values()
            if paper_ids is None or e.paper_id in paper_ids
        ]
        return sorted(candidates, key=lambda x: x.retrieval_score, reverse=True)[:top_k]


def run_demo(corpus, retrieval, backend):
    executor = ToolExecutor(create_paper_registry(corpus, retrieval))
    # 固定顺序由人工安排，没有自动选择下一动作的策略。
    first = executor.execute(
        "search_text",
        {
            "query": "What does the Perceiver Resampler do?",
            "paper_ids": ["flamingo"],
            "top_k": 1,
        },
        tool_call_id="day3-step-1",
    )
    second = executor.execute(
        "read_page", {"paper_id": "flamingo", "page": 11}, tool_call_id="day3-step-2"
    )
    context = " ".join(e["text"] for e in second.payload.get("evidence", []))
    return {
        "question": "Why does Flamingo use the Perceiver Resampler?",
        "backend": backend,
        "ranking_is_synthetic": backend == "fixture",
        "llm_connected": False,
        "agent_loop": False,
        "note": "Fixed manual two-call sequence. Fixture ranking is not semantic-retrieval evaluation.",
        "steps": [
            {
                "trace": trace.model_dump(mode="json"),
                "observation": obs.model_dump(mode="json"),
            }
            for trace, obs in zip(executor.traces, [first, second], strict=True)
        ],
        "checks": {
            "both_calls_successful": first.status == second.status == "success",
            "prepared_page_has_64_outputs": "64" in context,
            "prepared_page_explains_complexity": "computational complexity" in context,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["fixture", "real"], default="fixture")
    parser.add_argument("--papers-dir", type=Path, default=ROOT / "data/papers")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/chroma/day2")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    corpus = load_paper_corpus(args.papers_dir)
    if args.backend == "fixture":
        embedding, store = FixtureEmbedding(corpus), FixedVectorStore()
        ingest_text_corpus(corpus, embedding, store)
    else:
        from app.retrieval.embedding import QwenEmbeddingModel
        from app.retrieval.vector_store import ChromaTextVectorStore

        embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
        store = ChromaTextVectorStore(args.index_dir, "papers-day2-text")
    retrieval = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
    report = run_demo(corpus, retrieval, args.backend)
    if args.backend == "fixture":
        print(
            "Fixture mode: real paper evidence, synthetic ranking; no model was called."
        )
    for index, step in enumerate(report["steps"], 1):
        trace = step["trace"]
        print(
            f"Step {index} | Tool: {trace['tool_name']} | Status: {trace['status']} | "
            f"Evidence: {trace['result_count']} | Latency: {trace['latency_ms']:.2f} ms"
        )
        print(step["observation"]["summary"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    date = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M%S-%f")
    path = (
        args.output_dir
        / f"{date}-多模态论文研究智能体-Day3-{args.backend}-手动工具演示-{stamp}.json"
    )
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Saved observations and execution facts:", path)
    if not all(report["checks"].values()):
        raise SystemExit(
            "Tool observations require review; the expected prepared context was not obtained."
        )


if __name__ == "__main__":
    main()
