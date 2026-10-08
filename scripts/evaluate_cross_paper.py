"""默认只校验；真实检索/研究必须显式执行，脚本化结果只证明工程连线。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

from app.evidence.corpus import load_paper_corpus
from app.evidence.models import TextEvidence
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.papers.catalog import load_paper_catalog
from app.papers.evidence_access import DiscoveryEvidenceRepository
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.corpus_validation import (
    canonical_hash,
    corpus_snapshot,
    validate_index_records,
)
from app.research.orchestrator import AsyncResearchOrchestrator
from app.research.recovery_models import (
    RecoveryBudget,
    RecoveryConfig,
    RecoveryFeedback,
    claim_ref,
)
from app.research.recovery_orchestrator import RecoveryOrchestrator
from app.research.review_runtime import EvidenceReviewRuntime, ReviewRuntimeConfig
from app.research.review_validation import DeterministicReviewValidator
from app.research.reviewer import EvidenceReviewer
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.synthesis_agent import SynthesisAgent
from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_models import SynthesisInput
from app.research.synthesis_runtime import SynthesisRuntime
from app.research.worker import PaperResearchWorker
from app.tools.executor import ToolExecutor
from app.tools.paper import FindFigureTool, ReadPageTool
from app.tools.registry import ToolRegistry
from eval.cross_paper import CrossPaperDataset, research_metrics, retrieval_metrics
from scripts.smoke_research_synthesis import render_markdown

ROOT = Path(__file__).resolve().parents[1]


def action(name, arguments):
    return LLMResponse(
        tool_calls=[ToolCall(id="stage-e-scripted", name=name, arguments=arguments)]
    )


def scripted_report(messages, tools):
    """仅根据 Builder 的已核验输入构造结构；不创造自由文本结论。"""
    source = SynthesisInput.model_validate(json.loads(messages[-1]["content"])["input"])
    by_id = {c.claim_id: c for c in source.verified_claims}
    sections = []
    for dim in source.plan.dimensions:
        claims = [
            c for c in source.verified_claims if c.dimension_id == dim.dimension_id
        ]
        sections.append(
            {
                "dimension_id": dim.dimension_id,
                "cells": [
                    c.model_dump(mode="json")
                    | {
                        "evidence_ids": by_id[c.claim_id].evidence_ids
                        if c.claim_id
                        else []
                    }
                    for c in source.cells
                    if c.dimension_id == dim.dimension_id
                ],
                "comparisons": [
                    {
                        "paper_ids": [c.paper_id for c in claims],
                        "claim_ids": [c.claim_id for c in claims],
                        "evidence_ids": sorted(
                            {e for c in claims for e in c.evidence_ids}
                        ),
                    }
                ]
                if len({c.paper_id for c in claims}) >= 2
                else [],
            }
        )
    return action("submit_report", {"sections": sections})


def index_records(store):
    data = store.collection.get(include=["documents", "metadatas"])
    records = []
    for identity, text, meta in zip(
        data["ids"], data["documents"], data["metadatas"], strict=True
    ):
        if meta is None or identity != meta.get("evidence_id"):
            raise ValueError("index_identity_mismatch")
        records.append(TextEvidence(**meta, text=text))
    return records


def real_retriever(config, corpus, snapshot, *, embedding=None):
    """独立研究索引，不写入 V0.1 collection；成功校验后才建立向量。"""
    from app.retrieval.embedding import QwenEmbeddingModel
    from app.retrieval.ingestion import ingest_text_corpus
    from app.retrieval.retriever import TextRetriever
    from app.retrieval.vector_store import ChromaTextVectorStore

    store = ChromaTextVectorStore(
        ROOT / config["index_dir"], config["collection"], create=True
    )
    binding = {
        "corpus_snapshot_id": snapshot["snapshot_id"],
        "embedding_model": config["embedding_model"],
        "distance": "cosine",
        "schema_version": 1,
    }
    if store.collection.count() and store.collection.metadata != binding:
        raise ValueError(
            "Index configuration or corpus snapshot changed; use a new index directory"
        )
    if embedding is None:
        embedding = QwenEmbeddingModel(
            config["embedding_model"], device=config["device"]
        )
    if not store.collection.count():
        ingest_text_corpus(corpus, embedding, store)
        store.collection.modify(metadata=binding)
    validate_index_records(corpus, index_records(store))
    return TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)


async def run_case(case, corpus, snapshot, config, *, scripted, retriever=None):
    started = perf_counter()
    runtime_config = ResearchRuntimeConfig(**config["runtime"])
    gate = ResourceGate(runtime_config)
    catalog = load_paper_catalog()
    resolver = EvidenceResolver(corpus, paper_catalog=catalog)
    clients, workers = [], []
    review_config = ReviewRuntimeConfig(**config["review_runtime"])
    recovery_config = RecoveryConfig(**config["recovery"])
    if (
        len(case.plan.paper_ids)
        * (
            runtime_config.max_model_calls_per_worker
            + len(case.plan.dimensions) * review_config.max_model_calls_per_review
        )
        + 5
        > recovery_config.max_research_model_calls
    ):
        raise ValueError("Initial worst-case budget does not reserve synthesis")

    def model_client(responses=None):
        if scripted:
            return ScriptedLLMClient(responses or [])
        from app.llm.provider import OpenAICompatibleLLMClient

        client = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=runtime_config.operation_timeout_seconds,
        )
        clients.append(client)
        return client

    def worker_factory(task, recovery_context=None):
        papers = DiscoveryEvidenceRepository(corpus, catalog)
        registry = ToolRegistry()
        registry.register(ReadPageTool(papers))
        registry.register(FindFigureTool(papers))
        if not scripted:
            from app.tools.discovery import DiscoveryScopedTextTool
            from app.tools.figure import AnalyzeFigureTool
            from app.vision.analysis import FigureAnalyzer
            from app.vision.provider import OpenAICompatibleVisionClient

            registry.register(DiscoveryScopedTextTool(retriever, papers, catalog))
            vision = OpenAICompatibleVisionClient(
                api_key=os.environ["VLM_API_KEY"],
                model=os.environ["VLM_MODEL"],
                base_url=os.environ["VLM_BASE_URL"],
                timeout=runtime_config.operation_timeout_seconds,
            )
            clients.append(vision)
            registry.register(AnalyzeFigureTool(resolver, FigureAnalyzer(vision)))
        responses = []
        if scripted:
            item = next(e for e in corpus.text_evidence if e.paper_id == task.paper_id)
            responses = [
                action("read_page", {"paper_id": task.paper_id, "page": item.page}),
                action(
                    "resolve_requirements",
                    {
                        "resolutions": [
                            {
                                "requirement_id": r.requirement_id,
                                "status": "supported",
                                "claim": item.text,
                                "evidence_ids": [item.evidence_id],
                            }
                            for r in task.requirements
                        ]
                    },
                ),
            ]
        worker = PaperResearchWorker(
            AsyncLLMAdapter(model_client(responses), gate),
            AsyncToolExecutorAdapter(ToolExecutor(registry), gate),
            resolver,
            runtime_config,
            recovery_context=recovery_context,
        )
        workers.append(worker)
        return worker

    review_runtime = EvidenceReviewRuntime(
        lambda task: EvidenceReviewer(
            AsyncLLMAdapter(
                model_client(
                    [
                        action(
                            "submit_review",
                            {
                                "status": "passed",
                                "reason_code": "evidence_supports_claim",
                            },
                        )
                    ]
                ),
                gate,
            ),
            max_model_calls=review_config.max_model_calls_per_review,
        ),
        DeterministicReviewValidator(resolver),
        review_config,
        gate,
    )
    synthesis = SynthesisRuntime(
        SynthesisInputBuilder(resolver),
        SynthesisAgent(AsyncLLMAdapter(model_client([scripted_report]), gate)),
    )
    pool = AsyncResearchOrchestrator(worker_factory, runtime_config, gate)
    recovery = RecoveryOrchestrator(
        lambda task: worker_factory(task.worker_task(), task),
        review_runtime,
        resolver,
        {e.paper_id for e in corpus.text_evidence + corpus.figure_evidence},
        config=recovery_config,
        synthesis_runtime=synthesis,
    )
    initial_workers, initial_reviews = [], []

    def partial_snapshot(status, issue_type):
        current_workers = initial_workers or pool.last_results
        current_reviews = initial_reviews or review_runtime.last_results
        return {
            "case_id": case.case_id,
            "status": status,
            "issue_type": issue_type,
            "real_provider_used": not scripted,
            "quality_result_usable": False,
            "planner_mode": config["planner_mode"],
            "planner_model_calls": 0,
            "plan": case.plan.model_dump(mode="json"),
            "configuration": config,
            "corpus_snapshot": snapshot,
            "initial_worker_results": [
                w.model_dump(mode="json") for w in current_workers
            ],
            "initial_review_results": [
                r.model_dump(mode="json") for r in current_reviews
            ],
            "outcome": recovery.last_outcome.model_dump(mode="json")
            if recovery.last_outcome
            else None,
            "llm_provider_attempts_observed": sum(
                len(getattr(c, "calls", [])) for c in clients
            ),
            "known_initial_model_calls": sum(
                w.trace.model_calls for w in current_workers if w.trace
            )
            + sum(r.trace.model_calls for r in current_reviews),
            "known_initial_tool_calls": sum(
                w.trace.tool_calls for w in current_workers if w.trace
            ),
            "wall_time_ms": (perf_counter() - started) * 1000,
            "counting_limitations": "In-flight cancelled operations may finish during drain; these counters are snapshots, not billing totals.",
        }

    try:
        initial_workers = await pool.run(case.worker_tasks())
        initial_reviews = await review_runtime.run(initial_workers)
        lookup = {r.requirement_id: r for w in initial_workers for r in w.requirements}
        # Reviewer 没有解释字段，反馈只转达真实拒绝类型，不能编造审核原因。
        feedback = [
            RecoveryFeedback(
                requirement_id=r.requirement_id,
                rejected_claim_id=claim_ref(lookup[r.requirement_id]),
                reason_code=r.reason_code,
                explanation="The Reviewer rejected this exact claim as "
                + r.reason_code
                + ". Inspect the supplied evidence and original requirement; narrow the claim or locate directly supporting paper-local evidence. Do not infer a more specific review rationale.",
                prior_evidence_ids=r.evidence_ids,
            )
            for r in initial_reviews
            if r.status == "rejected" and r.validation_status == "passed"
        ]
        prior = RecoveryBudget(
            model_calls=sum(w.trace.model_calls for w in initial_workers if w.trace)
            + sum(r.trace.model_calls for r in initial_reviews),
            tool_calls=sum(w.trace.tool_calls for w in initial_workers if w.trace),
        )
        outcome = await recovery.run(
            case.plan, initial_workers, initial_reviews, feedback, prior_usage=prior
        )
        status = "scripted_wiring_only" if scripted else "completed"
        if not scripted and all(w.status == "failed" for w in initial_workers):
            status = "not_executed"
        metrics = research_metrics(
            outcome,
            initial_workers=initial_workers,
            initial_reviews=initial_reviews,
            wall_time_ms=(perf_counter() - started) * 1000,
            corpus_report=snapshot,
        )
        if scripted:
            metrics["coverage_delta_vs_stage_d"] = None
            metrics["unsupported_claim_detection_method"] = (
                "Scripted Reviewer verdicts test wiring only; no semantic unsupported-claim measurement."
            )
        return {
            "case_id": case.case_id,
            "status": status,
            "real_provider_used": not scripted,
            "real_provider_calls": 0 if scripted else None,
            "llm_provider_attempts_observed": sum(
                len(getattr(c, "calls", [])) for c in clients
            ),
            "models_initialized": 0 if scripted else 1,
            "planner_mode": config["planner_mode"],
            "planner_model_calls": 0,
            "plan": case.plan.model_dump(mode="json"),
            "corpus_snapshot": snapshot,
            "configuration": config,
            "models": {
                "llm": "scripted" if scripted else os.environ["LLM_MODEL"],
                "vlm": "scripted" if scripted else os.environ["VLM_MODEL"],
                "embedding": None if scripted else config["embedding_model"],
                "temperature": config["provider_temperature"],
            },
            "initial_worker_results": [
                w.model_dump(mode="json") for w in initial_workers
            ],
            "initial_review_results": [
                r.model_dump(mode="json") for r in initial_reviews
            ],
            "orchestrator_trace": pool.trace.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "metrics": metrics,
            "quality_result_usable": not scripted and status == "completed",
            "limitations": [
                "Scripted Reviewer passes test transport/identity only, never semantics."
            ]
            if scripted
            else [
                "Frozen real Planner output is replayed, not regenerated; zero new Planner calls.",
                "Model-based review is not independently measured semantic correctness.",
                "Worker model_calls includes conservative VLM tool-attempt reservations, not exact provider billing.",
            ],
        }
    except asyncio.CancelledError as exc:
        exc.evaluation_snapshot = partial_snapshot("cancelled", "CancelledError")
        raise
    except (
        ValueError,
        RuntimeError,
        TypeError,
        AttributeError,
        KeyError,
        OSError,
    ) as exc:
        # 已启动研究时保留实际结果和调用计数，不能退回到“0次调用”的预检报告。
        return partial_snapshot("execution_failed", type(exc).__name__)
    finally:
        try:
            await asyncio.gather(
                *(w.drain() for w in workers),
                review_runtime.drain(),
                synthesis.drain(),
                recovery.drain(),
            )
        finally:
            for client in clients:
                client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/cross_paper_eval.json"
    )
    parser.add_argument("--case-id", default="two-paper-bridge")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scripted-smoke", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--execute-retrieval", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    corpus = load_paper_corpus(
        ROOT / config["papers_dir"],
        additional_papers_dir=ROOT / config["additional_papers_dir"],
    )
    snapshot = corpus_snapshot(
        ROOT / config["papers_dir"], corpus, ROOT / config["additional_papers_dir"]
    )
    dataset = CrossPaperDataset.model_validate_json(
        (ROOT / config["dataset"]).read_text()
    )
    dataset.validate_corpus(corpus, snapshot["snapshot_id"])
    if config["corpus_snapshot_id"] != snapshot["snapshot_id"] or config["top_k"] != 10:
        parser.error("Frozen corpus/top_k configuration mismatch")
    case = next((c for c in dataset.cases if c.case_id == args.case_id), None)
    if case is None:
        parser.error("Unknown case ID")
    report = {
        "status": "not_executed",
        "case_id": case.case_id,
        "reason": "user_confirmation_required_before_real_evaluation",
        "real_provider_calls": 0,
        "models_initialized": 0,
        "corpus_snapshot": snapshot,
        "dataset_version": dataset.version,
        "dataset_sha256": canonical_hash(dataset.model_dump(mode="json")),
        "validated_cases": len(dataset.cases),
        "core_requirements": len(dataset.core_requirement_ids),
        "configuration": config,
        "worker_tasks": [t.model_dump(mode="json") for t in case.worker_tasks()],
        "retrieval_metrics": {
            "status": "not_executed",
            "planned_sample_count": 15,
            "recall_at_5": None,
            "recall_at_10": None,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.execute or args.execute_retrieval:
        if case.controlled_only:
            parser.error(
                "Controlled attack/recovery cases are verified by contract tests, not unlabeled real runs"
            )
        if args.execute:
            from dotenv import load_dotenv

            load_dotenv(args.env_file, override=False)
            if any(
                not os.environ.get(f"{kind}_{field}")
                for kind in ("LLM", "VLM")
                for field in ("API_KEY", "MODEL", "BASE_URL")
            ):
                report["reason"] = "provider_configuration_unavailable"
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print("not_executed: provider_configuration_unavailable")
                return
        try:
            retriever = real_retriever(config, corpus, snapshot)
            if args.execute_retrieval:
                before_corpus = load_paper_corpus(ROOT / config["papers_dir"])
                before_snapshot = corpus_snapshot(
                    ROOT / config["papers_dir"], before_corpus
                )
                before = real_retriever(
                    config | {"collection": config["collection"] + "-before"},
                    before_corpus,
                    before_snapshot,
                    embedding=retriever.embedding_model,
                )
                core = [c for c in dataset.cases if c.case_id.startswith("core-")]
                report.update(
                    status="retrieval_executed",
                    retrieval_metrics={
                        "before_stage_d_corpus": retrieval_metrics(core, before),
                        "after_stage_e_corpus": retrieval_metrics(core, retriever),
                        "label_policy": config["source_label_policy"],
                    },
                    models_initialized=1,
                )
            else:
                report = asyncio.run(
                    run_case(
                        case,
                        corpus,
                        snapshot,
                        config,
                        scripted=False,
                        retriever=retriever,
                    )
                )
        except asyncio.CancelledError as exc:
            report = getattr(
                exc,
                "evaluation_snapshot",
                report | {"status": "cancelled", "reason": "evaluation_cancelled"},
            )
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            raise
        except (ValueError, OSError, RuntimeError) as exc:
            report.update(
                status="not_executed",
                reason="initialization_or_execution_failure",
                issue_type=type(exc).__name__,
            )
    elif args.scripted_smoke:
        if case.category not in {"single_fact", "two_paper", "three_paper"}:
            parser.error(
                "Generic scripted smoke verifies report wiring only; use specialized contract tests for adversarial cases"
            )
        report = asyncio.run(run_case(case, corpus, snapshot, config, scripted=True))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if report.get("outcome") and report["outcome"].get("report"):
        from app.research.synthesis_models import ResearchReport

        prefix = (
            "验证类型：Scripted/Fake 工程连线，不能作为研究质量或 Benchmark 结果。\n\n"
            if args.scripted_smoke
            else "验证类型：真实模型工程评测；沿用原始 Planner 计划。\n\n"
        )
        args.output.with_suffix(".md").write_text(
            prefix
            + render_markdown(
                ResearchReport.model_validate(report["outcome"]["report"])
            )
        )
    print(
        json.dumps(
            {
                "case_id": case.case_id,
                "status": report["status"],
                "metrics": report.get("metrics"),
                "retrieval_metrics": report.get("retrieval_metrics"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
