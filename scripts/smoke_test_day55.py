"""Run four REAL decision-model cases through the unchanged Day 5 controller."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from app.agent.loop import BoundedToolAgent
from app.diagnostics import failure_details
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.llm.provider import OpenAICompatibleLLMClient
from app.retrieval.embedding import QwenEmbeddingModel
from app.retrieval.retriever import TextRetriever
from app.retrieval.vector_store import ChromaTextVectorStore
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.tools.redaction import public_value
from app.vision.analysis import FigureAnalyzer
from app.vision.provider import OpenAICompatibleVisionClient
from scripts.demo_day5 import ControlledRetrieval

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("/mnt/d/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体")
CASES = [
    ("TextQA", "What training objective does CLIP use to align images and text?"),
    ("FigureQA", "Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。"),
    ("FlamingoTextQA", "Why does Flamingo use the Perceiver Resampler?"),
    (
        "InsufficientEvidence",
        "What is the exact SHA-256 of the private dataset manifest used to train BLIP, and the number of duplicate files removed during preprocessing? Use only the prepared corpus.",
    ),
]


class TimedVisionClient:
    """Instrumentation only; delegates unchanged requests to the existing VLM."""

    def __init__(self, client, decision_client):
        self.client, self.calls = client, []
        self.decision_client = decision_client

    def analyze(self, image_path, prompt):
        started = perf_counter()
        status = "error"
        failure = None
        try:
            response = self.client.analyze(image_path, prompt)
            status = "success"
            return response
        except Exception as exc:
            failure = failure_details(
                exc, phase="vlm_request", summary="Vision model request failed."
            )
            raise
        finally:
            actions = self.decision_client.calls[-1].get("tool_calls", [])
            call_id = actions[0]["id"] if len(actions) == 1 else None
            self.calls.append(
                {
                    "tool_call_id": call_id,
                    "failure": failure,
                    "status": status,
                    "latency_ms": (perf_counter() - started) * 1000,
                }
            )


def evidence_ids(payload):
    records = [*payload.get("evidence", []), *payload.get("figures", [])]
    if payload.get("figure"):
        records.append(payload["figure"])
    return list(
        dict.fromkeys(
            r["evidence_id"]
            for r in records
            if isinstance(r, dict) and "evidence_id" in r
        )
    )


def run_cases(corpus, retrieval, llm, vision):
    reports = []
    for name, question in CASES:
        llm_start, vlm_start = len(llm.calls), len(vision.calls)
        executor = ToolExecutor(
            create_paper_registry(corpus, retrieval, analyzer=FigureAnalyzer(vision))
        )
        result = BoundedToolAgent(llm, executor, EvidenceResolver(corpus)).run(question)
        serialized = public_value(result.model_dump(mode="json"))
        llm_events, vlm_events = llm.calls[llm_start:], vision.calls[vlm_start:]
        llm_events = [
            {"model_step": i, **event} for i, event in enumerate(llm_events, 1)
        ]
        tools = []
        for trace in result.state.trace:
            paired = [
                m
                for m in result.state.messages
                if m["role"] == "tool" and m["tool_call_id"] == trace.tool_call_id
            ]
            observation = json.loads(paired[0]["content"]) if paired else {}
            tool_vlm_ms = sum(
                event["latency_ms"]
                for event in vlm_events
                if event.get("tool_call_id") == trace.tool_call_id
            )
            tools.append(
                {
                    "T_VLM_ms": tool_vlm_ms,
                    **trace.model_dump(mode="json"),
                    "evidence_ids": evidence_ids(observation.get("payload", {})),
                    "observation": observation,
                }
            )
        llm_ms = sum(e["latency_ms"] for e in llm_events)
        vlm_ms = sum(e["latency_ms"] for e in vlm_events)
        tools_ms = sum(t.latency_ms for t in result.state.trace)
        observed = result.state.observed_evidence_ids
        names = [t.tool_name for t in result.state.trace]
        report = {
            "case": name,
            "question": question,
            "backend": "real_llm + injected_unchanged_retrieval + real_vlm",
            "N_tools": result.tool_calls,
            "N_model": result.model_calls,
            "N_VLM": len(vlm_events),
            "T_total_ms": result.total_latency_ms,
            "T_LLM_ms": llm_ms,
            "T_Tool_inclusive_ms": tools_ms,
            "T_Tool_exclusive_ms": max(0, tools_ms - vlm_ms),
            "T_VLM_ms": vlm_ms,
            "controller_overhead_ms": result.total_latency_ms - llm_ms - tools_ms,
            "timing_note": "VLM latency is nested inside analyze_figure tool latency. Add LLM + Tool-exclusive + VLM; do not add VLM to Tool-inclusive again. Initialization excluded.",
            "tools": tools,
            "llm_requests": llm_events,
            "vlm_requests": vlm_events,
            "answer": serialized["answer"],
            "citations": serialized["citations"],
            "limitations": serialized["limitations"],
            "stop_reason": result.status,
            "stop_details": serialized["stop_details"],
            "agent_state": serialized["state"],
            "checks": {
                "tool_budget_respected": result.tool_calls <= 6,
                "citation_ids_observed": all(
                    c.evidence_id in observed for c in result.citations
                ),
                "text_qa_no_vlm": "analyze_figure" not in names
                if name in {"TextQA", "FlamingoTextQA"}
                else None,
                "figure_tools_selected": "find_figure" in names
                and "analyze_figure" in names
                if name == "FigureQA"
                else None,
                "insufficient_finish_uncited": result.status == "insufficient_evidence"
                and not result.citations
                and bool(result.limitations)
                if name == "InsufficientEvidence"
                else None,
            },
            "semantic_review": "pending: inspect actual answers and supplied evidence; path checks do not prove task success",
        }
        reports.append(public_value(report))
        print(
            f"{name}: {result.status}, N_tools={result.tool_calls}, N_model={result.model_calls}, N_VLM={len(vlm_events)}"
        )
        if result.stop_details:
            print(
                "  Stop details:",
                json.dumps(public_value(result.stop_details), ensure_ascii=False),
            )
        for tool_event in tools:
            if tool_event.get("failure"):
                print(
                    f"  Tool {tool_event['tool_name']} ({tool_event['status']}):",
                    json.dumps(tool_event["failure"], ensure_ascii=False),
                )
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit local configuration file; never read sibling project keys implicitly",
    )
    parser.add_argument(
        "--retrieval-backend",
        choices=["day5-controlled", "real"],
        default="day5-controlled",
        help="Default retains the Day 5 retrieval fixture; real opts into the existing Day 2 index",
    )
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/chroma/day2")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv

        if not args.env_file.is_file():
            parser.error("Specified local configuration file does not exist")
        load_dotenv(args.env_file, override=False)
    required = [
        f"{kind}_{field}"
        for kind in ["LLM", "VLM"]
        for field in ["API_KEY", "MODEL", "BASE_URL"]
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        parser.error("Missing model configuration variables: " + ", ".join(missing))
    corpus = load_paper_corpus(ROOT / "data/papers")
    # Same domain implementations and existing index; no automatic ingestion or ranking modification.
    if args.retrieval_backend == "day5-controlled":
        retrieval = ControlledRetrieval(corpus)
    else:
        embedding = QwenEmbeddingModel(args.embedding_model, device=args.device)
        store = ChromaTextVectorStore(args.index_dir, "papers-day2-text")
        retrieval = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
    llm = OpenAICompatibleLLMClient(
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
        base_url=os.environ["LLM_BASE_URL"],
    )
    vision_client = OpenAICompatibleVisionClient(
        api_key=os.environ["VLM_API_KEY"],
        model=os.environ["VLM_MODEL"],
        base_url=os.environ["VLM_BASE_URL"],
    )
    try:
        reports = run_cases(
            corpus, retrieval, llm, TimedVisionClient(vision_client, llm)
        )
    finally:
        llm.close()
        vision_client.close()
    stamp = datetime.now(ZoneInfo("Asia/Shanghai"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{stamp:%Y-%m-%d}-多模态论文研究智能体-Day5.5"
    metadata = {
        "status": "requires_semantic_review",
        "LLM_model": os.environ["LLM_MODEL"],
        "VLM_model": os.environ["VLM_MODEL"],
        "retrieval_backend": args.retrieval_backend,
        "embedding_model": args.embedding_model
        if args.retrieval_backend == "real"
        else None,
        "note": "Real decision-model smoke run. Default retrieval retains Day 5 controlled fixture without new ranking rules. Real VLM replaces the demonstration fake; a strict policy-only cost ablation must use this same VLM on both policy arms. Runtime, policy prompt, tool contracts and corpus unchanged.",
        "cases": reports,
    }
    (args.output_dir / f"{prefix}-四条真实Trace-{stamp:%H%M%S-%f}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for report in reports:
        (
            args.output_dir
            / f"{prefix}-{report['case']}-真实Trace-{stamp:%H%M%S-%f}.json"
        ).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Saved four real traces under {args.output_dir}")
    if any(
        r["stop_reason"]
        in {"model_failure", "protocol_failure", "step_exhaustion", "repeated_failure"}
        for r in reports
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
