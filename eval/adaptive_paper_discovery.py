"""同一冻结 8 题比较单查询与模型自主扩展；并集不冒充融合排名。"""

import argparse
import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter

from pydantic import Field, StrictInt

from app.agent.loop import BoundedToolAgent
from app.api.runtime import create_configured_runtime
from app.evidence.resolver import EvidenceResolver
from app.llm.client import LLMClient
from app.papers.candidates import paper_searches, union_candidates
from app.papers.catalog import DEFAULT_FIXTURE
from app.papers.expansion import QueryExpander
from app.papers.retriever import PaperRetriever
from app.tools.discovery import SearchPapersInput, SearchPapersTool
from app.tools.executor import ToolExecutor
from app.tools.expansion import ExpandSearchQueryTool
from app.tools.registry import ToolRegistry
from eval.paper_discovery import DEFAULT_QUERIES, load_queries

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_EVAL_POLICY = """This request is a discovery-only task, not paper-body QA.
Search the exact original user query first with top_k=10. Read the observation.
You may use expand_search_query if a set-valued open question has meaningful perspectives
not covered by that first search. Do not expand merely because a tool is available.
Highly specific known-item descriptions usually need no expansion. You do not know gold labels.
Only the original query and validated alternatives can be searched; top_k is always 10.
Expansion is optional; decide whether zero, one or two alternatives are worth executing.
Finish when discovery is adequate. Use evidence_ids=[] and explicitly state that this is
metadata-only discovery, no scientific claims or evidence-grounded QA have been performed.
Do not try unavailable body tools. Do not name a missing target to manipulate retrieval.
"""


class EvaluationSearchInput(SearchPapersInput):
    top_k: StrictInt = Field(default=10, ge=10, le=10)


class EvaluationSearchTool(SearchPapersTool):
    input_model = EvaluationSearchInput


class MeasuredEmbedding:
    def __init__(self, embedding):
        self.embedding = embedding
        self.events = []

    def embed_query(self, query):
        started = perf_counter()
        try:
            return self.embedding.embed_query(query)
        finally:
            self.events.append(
                {"query": query, "latency_ms": (perf_counter() - started) * 1000}
            )


class DiscoveryDecisionClient(LLMClient):
    def __init__(self, llm):
        self.llm = llm
        self.events = []

    def complete(self, *, messages, tools):
        messages = [dict(m) for m in messages]
        messages[0]["content"] += "\n" + DISCOVERY_EVAL_POLICY
        started = perf_counter()
        event = {"status": "error"}
        try:
            response = self.llm.complete(messages=messages, tools=tools)
            event.update(
                status="success",
                actions=[c.model_dump(mode="json") for c in response.tool_calls],
            )
            return response
        finally:
            event["latency_ms"] = (perf_counter() - started) * 1000
            self.events.append(event)


def recall(ids, relevant):
    return len(set(ids) & set(relevant)) / len(set(relevant))


def comparison_metrics(baseline_ids, observations, relevant):
    """固定总候选 K 与每条查询 Top-K 并集覆盖分开，显式报告候选规模。"""
    merged = union_candidates(observations)
    searches = paper_searches(observations)
    merged_ids = [p["paper_id"] for p in merged]
    result = {}
    for k in (5, 10):
        baseline = recall(baseline_ids[:k], relevant)
        fixed = recall(merged_ids[:k], relevant)
        per_query_union = list(
            dict.fromkeys(p["paper_id"] for s in searches for p in s["papers"][:k])
        )
        coverage = recall(per_query_union, relevant)
        result[f"recall_at_{k}"] = {
            "baseline": baseline,
            "adaptive_fixed_total_k": fixed,
            "adaptive_union_per_query_k": coverage,
        }
        result[f"expansion_gain_at_{k}"] = {
            "fixed_total_k": fixed - baseline,
            "union_per_query_k": coverage - baseline,
        }
        result[f"union_candidate_count_at_{k}"] = len(per_query_union)
    result["new_relevant_paper_ids"] = sorted(
        (set(merged_ids) - set(baseline_ids)) & set(relevant)
    )
    result["union_candidates"] = merged
    return result


def summarize_report(report):
    rows = report["cases"]
    report["expansion_rate"] = sum(r["expansion_attempted"] for r in rows) / len(rows)
    report["macro_recall"] = {
        f"at_{k}": {
            key: sum(r[f"recall_at_{k}"][key] for r in rows) / len(rows)
            for key in [
                "baseline",
                "adaptive_fixed_total_k",
                "adaptive_union_per_query_k",
            ]
        }
        for k in (5, 10)
    }
    report["cost_totals"] = {
        key: sum(r[key] for r in rows)
        for key in [
            "additional_llm_calls",
            "additional_embedding_calls",
            "additional_latency_ms",
        ]
    }
    report["targeted_checks"] = {}
    for case_id, identity in [
        ("discovery-03", "instructblip-2305.06500v1"),
        ("discovery-08", "ofa-2202.03052v1"),
    ]:
        row = next(r for r in rows if r["case_id"] == case_id)
        sources = [
            {"query": o["payload"]["query"], "rank": rank}
            for o in row["observations"]
            if o["tool_name"] == "search_papers"
            and o["status"] == "success"
            and o["payload"]["query"] != row["query"]
            for rank, p in enumerate(o["payload"]["papers"], 1)
            if p["paper_id"] == identity
        ]
        report["targeted_checks"][case_id] = {
            "paper_id": identity,
            "baseline_at_10": identity in row["baseline"]["paper_ids"],
            "retrieved_by_new_query": bool(sources),
            "sources": sources,
        }
    report["successful_expansion_rate"] = sum(
        bool(r["expanded_queries"]) for r in rows
    ) / len(rows)
    report["cost_summary"] = {
        system: {
            "model_calls": sum(r[system]["model_calls"] for r in rows),
            "embedding_calls": sum(r[system]["embedding_calls"] for r in rows),
            "mean_latency_ms": sum(r[system]["latency_ms"] for r in rows) / len(rows),
        }
        for system in ("baseline", "adaptive")
    }
    report["status"] = "completed"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/adaptive-paper-discovery.json"
    )
    args = parser.parse_args()
    os.environ["AGENT_ENV_FILE"] = str(args.env_file.resolve())
    os.environ["AGENT_PAPER_DISCOVERY"] = "1"
    runtime = create_configured_runtime()
    catalog = runtime.paper_catalog
    cases = load_queries(DEFAULT_QUERIES, catalog)
    fixture_hash = hashlib.sha256(DEFAULT_FIXTURE.read_bytes()).hexdigest()
    query_hash = hashlib.sha256(DEFAULT_QUERIES.read_bytes()).hexdigest()
    report = {
        "scope": "Same Phase 1 queries, corpus, model and labels; real adaptive policy; no QA benchmark.",
        "fixture_sha256": fixture_hash,
        "queries_sha256": query_hash,
        "embedding_model": runtime.paper_retriever.vector_store.collection.metadata[
            "embedding_model"
        ],
        "annotation_status": "development_candidate_labels_not_user_reviewed",
        "union_protocol": "Stable first-seen paper_id union; no fusion or reranking. Global Top-K and union of per-query Top-K reported separately.",
        "cases": [],
        "status": "running",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        baseline_embedding = MeasuredEmbedding(runtime.paper_retriever.embedding_model)
        baseline_retriever = PaperRetriever(
            baseline_embedding, runtime.paper_retriever.vector_store
        )
        started = perf_counter()
        baseline = baseline_retriever.search(case.query, 10)
        baseline_latency = (perf_counter() - started) * 1000
        baseline_ids = [p.paper.paper_id for p in baseline]
        with ExitStack() as stack:
            llm, _ = runtime.client_factory(stack)
            policy = DiscoveryDecisionClient(llm)
            expander = QueryExpander(llm)
            embedding = MeasuredEmbedding(runtime.paper_retriever.embedding_model)
            retriever = PaperRetriever(embedding, runtime.paper_retriever.vector_store)
            registry = ToolRegistry()
            registry.register(
                EvaluationSearchTool(
                    retriever, catalog, [m.paper_id for m in runtime.corpus.metadata]
                )
            )
            registry.register(ExpandSearchQueryTool(expander))
            executor = ToolExecutor(registry)
            started = perf_counter()
            result = BoundedToolAgent(
                policy,
                executor,
                EvidenceResolver(runtime.corpus, paper_catalog=catalog),
            ).run(case.query)
            adaptive_latency = (perf_counter() - started) * 1000
            searches = paper_searches(result.state.observations)
            expansions = [
                o.payload
                for o in result.state.observations
                if o.tool_name == "expand_search_query" and o.status == "success"
            ]
            metrics = comparison_metrics(
                baseline_ids, result.state.observations, case.relevant_paper_ids
            )
            row = {
                **case.model_dump(),
                **metrics,
                "original_query_retained": bool(searches)
                and searches[0]["query"] == case.query,
                "expansion_attempted": expander.calls > 0,
                "expanded_queries": [
                    q for output in expansions for q in output["expanded_queries"]
                ],
                "executed_queries": [s["query"] for s in searches],
                "baseline": {
                    "model_calls": 0,
                    "embedding_calls": len(baseline_embedding.events),
                    "latency_ms": baseline_latency,
                    "paper_ids": baseline_ids,
                },
                "adaptive": {
                    "decision_llm_calls": len(policy.events),
                    "expansion_llm_calls": expander.calls,
                    "model_calls": len(policy.events) + expander.calls,
                    "embedding_calls": len(embedding.events),
                    "latency_ms": adaptive_latency,
                    "tool_calls": result.tool_calls,
                    "stop_reason": result.status,
                    "metadata_discovery_finished": any(
                        o.tool_name == "finish" and o.status == "success"
                        for o in result.state.observations
                    ),
                },
                "additional_llm_calls": len(policy.events) + expander.calls,
                "additional_embedding_calls": len(embedding.events)
                - len(baseline_embedding.events),
                "additional_latency_ms": adaptive_latency - baseline_latency,
                "trace": [t.model_dump(mode="json") for t in result.state.trace],
                "observations": [
                    o.model_dump(mode="json") for o in result.state.observations
                ],
                "decision_events": policy.events,
                "expansion_events": expander.events,
                "embedding_events": embedding.events,
            }
            report["cases"].append(row)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(
                case.case_id,
                "expanded=",
                row["expansion_attempted"],
                "queries=",
                len(searches),
                "new_relevant=",
                row["new_relevant_paper_ids"],
                flush=True,
            )
    summarize_report(report)
    assert fixture_hash == hashlib.sha256(DEFAULT_FIXTURE.read_bytes()).hexdigest()
    assert query_hash == hashlib.sha256(DEFAULT_QUERIES.read_bytes()).hexdigest()
    report["status"] = "completed"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        "ExpansionRate=",
        report["expansion_rate"],
        "Recall=",
        report["macro_recall"],
        flush=True,
    )


if __name__ == "__main__":
    main()
