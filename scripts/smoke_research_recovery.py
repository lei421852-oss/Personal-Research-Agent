"""Stage D 三个明确分开的场景：受控初始拒绝+真实恢复，Fake再次拒绝，无语料跳过。"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.papers.catalog import load_paper_catalog
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.models import EvidenceRequirement, WorkerResult
from app.research.recovery_eligibility import RecoveryEligibilityEvaluator
from app.research.recovery_models import RecoveryBudget, RecoveryFeedback, claim_ref
from app.research.recovery_orchestrator import RecoveryOrchestrator
from app.research.review_models import ReviewResult, ReviewTrace
from app.research.review_runtime import EvidenceReviewRuntime
from app.research.review_validation import DeterministicReviewValidator
from app.research.reviewer import EvidenceReviewer
from app.research.runtime import ResourceGate
from app.research.synthesis_agent import SynthesisAgent
from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_models import SynthesisInput
from app.research.synthesis_runtime import SynthesisRuntime
from app.research.worker import PaperResearchWorker
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from scripts.smoke_async_research import ROOT, load_worker_tasks
from scripts.smoke_research_synthesis import render_markdown


def controlled_inputs(workers, reviews, mode):
    feedback = []
    if mode == "c":
        return workers, reviews, feedback
    workers = [WorkerResult.model_validate(w.model_dump()) for w in workers]
    reviews = [ReviewResult.model_validate(r.model_dump()) for r in reviews]
    worker = next(w for w in workers if w.paper_id.startswith("flamingo-"))
    original = worker.requirements[0]
    claim = (
        original.claim
        + " This architecture guarantees zero prediction errors on every downstream task and proves superior performance to all models."
    )
    target = EvidenceRequirement.model_validate(
        original.model_dump() | {"claim": claim}
    )
    index = workers.index(worker)
    workers[index] = WorkerResult.model_validate(
        worker.model_dump() | {"requirements": [target, *worker.requirements[1:]]}
    )
    review = next(r for r in reviews if r.requirement_id == target.requirement_id)
    reviews[reviews.index(review)] = ReviewResult.model_validate(
        review.model_dump()
        | {
            "status": "rejected",
            "reason_code": "evidence_partially_supports_claim",
            "claim": claim,
            "trace": ReviewTrace(),
        }
    )
    feedback.append(
        RecoveryFeedback(
            requirement_id=target.requirement_id,
            rejected_claim_id=claim_ref(target),
            reason_code="evidence_partially_supports_claim",
            explanation="The prepared pages support architecture components but do not establish zero errors or superiority to every model. Remove those unsupported guarantees, or find direct prepared evidence for them. Keep the original architecture question in scope.",
            prior_evidence_ids=target.evidence_ids,
        )
    )
    return workers, reviews, feedback


def submit(name, arguments):
    return LLMResponse(
        tool_calls=[ToolCall(id="controlled-smoke", name=name, arguments=arguments)]
    )


def scripted_synthesis(messages, tools):
    source = SynthesisInput.model_validate(json.loads(messages[-1]["content"])["input"])
    claims = {c.claim_id: c for c in source.verified_claims}
    return submit(
        "submit_report",
        {
            "sections": [
                {
                    "dimension_id": d.dimension_id,
                    "cells": [
                        c.model_dump(mode="json")
                        | {
                            "evidence_ids": claims[c.claim_id].evidence_ids
                            if c.claim_id
                            else []
                        }
                        for c in source.cells
                        if c.dimension_id == d.dimension_id
                    ],
                }
                for d in source.plan.dimensions
            ]
        },
    )


async def run_smoke(
    plan, workers, reviews, feedback, resolver, corpus, mode, prior, output
):
    gate = ResourceGate()
    clients = []
    retrieval = None

    def real_client():
        from app.llm.provider import OpenAICompatibleLLMClient

        client = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=60,
        )
        clients.append(client)
        return client

    def reviewer_factory(task):
        client = (
            real_client()
            if mode == "a"
            else ScriptedLLMClient(
                [
                    submit(
                        "submit_review",
                        {
                            "status": "rejected",
                            "reason_code": "evidence_does_not_support_claim",
                        },
                    )
                ]
            )
        )
        return EvidenceReviewer(AsyncLLMAdapter(client, gate))

    review_runtime = EvidenceReviewRuntime(
        reviewer_factory, DeterministicReviewValidator(resolver), resource_gate=gate
    )

    def worker_factory(task):
        nonlocal retrieval
        if mode == "c":
            raise AssertionError(
                "No Recovery Worker should be created for approved/insufficient papers"
            )
        registry = ToolRegistry()
        if mode == "a":
            from app.papers.evidence_access import DiscoveryEvidenceRepository
            from app.retrieval.embedding import QwenEmbeddingModel
            from app.retrieval.retriever import TextRetriever
            from app.retrieval.vector_store import ChromaTextVectorStore
            from app.tools.discovery import DiscoveryScopedTextTool
            from app.tools.figure import AnalyzeFigureTool
            from app.tools.paper import FindFigureTool, ReadPageTool
            from app.vision.analysis import FigureAnalyzer
            from app.vision.provider import OpenAICompatibleVisionClient

            catalog = load_paper_catalog()
            if retrieval is None:
                retrieval = TextRetriever(
                    QwenEmbeddingModel(
                        os.environ.get(
                            "AGENT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"
                        ),
                        device=os.environ.get("AGENT_DEVICE", "cpu"),
                    ),
                    ChromaTextVectorStore(
                        os.environ.get(
                            "AGENT_INDEX_DIR", str(ROOT / "data/chroma/day2")
                        ),
                        "papers-day2-text",
                    ),
                    paper_aliases=corpus.paper_aliases,
                )
            papers = DiscoveryEvidenceRepository(corpus, catalog)
            registry.register(DiscoveryScopedTextTool(retrieval, papers, catalog))
            registry.register(FindFigureTool(papers))
            registry.register(ReadPageTool(papers))
            vision = OpenAICompatibleVisionClient(
                api_key=os.environ["VLM_API_KEY"],
                model=os.environ["VLM_MODEL"],
                base_url=os.environ["VLM_BASE_URL"],
                timeout=60,
            )
            clients.append(vision)
            registry.register(AnalyzeFigureTool(resolver, FigureAnalyzer(vision)))
            client = real_client()
        else:
            req = task.worker_task().requirements[0]
            client = ScriptedLLMClient(
                [
                    submit(
                        "resolve_requirement",
                        {
                            "requirement_id": req.requirement_id,
                            "status": "supported",
                            "claim": "Controlled new claim rejected again by scripted Reviewer.",
                            "evidence_ids": task.reviewer_feedback[
                                0
                            ].prior_evidence_ids,
                        },
                    )
                ]
            )
        return PaperResearchWorker(
            AsyncLLMAdapter(client, gate),
            AsyncToolExecutorAdapter(ToolExecutor(registry), gate),
            resolver,
            recovery_context=task,
        )

    synthesis_client = (
        real_client() if mode == "a" else ScriptedLLMClient([scripted_synthesis])
    )
    synthesis = SynthesisRuntime(
        SynthesisInputBuilder(resolver),
        SynthesisAgent(AsyncLLMAdapter(synthesis_client, gate)),
    )
    prepared = {e.paper_id for e in [*corpus.text_evidence, *corpus.figure_evidence]}
    runtime = RecoveryOrchestrator(
        worker_factory, review_runtime, resolver, prepared, synthesis_runtime=synthesis
    )
    try:
        await runtime.run(plan, workers, reviews, feedback, prior_usage=prior)
    finally:
        try:
            if runtime.last_outcome:
                outcome = runtime.last_outcome
                report = {
                    "mode": {
                        "a": "controlled_initial_rejection_real_worker_reviewer_synthesis",
                        "b": "controlled_fake_repeat_rejection",
                        "c": "no_prepared_corpus_skip_scripted_synthesis",
                    }[mode],
                    "initial_rejection_is_controlled": mode != "c",
                    "real_provider_used": mode == "a",
                    "outcome": outcome.model_dump(mode="json"),
                }
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                if outcome.report:
                    prefix = (
                        "验证类型：受控初始拒绝；真实 Worker/Reviewer 与既有 Synthesis 执行一次恢复，不是自然失败率评测。\n\n"
                        if mode == "a"
                        else "验证类型：Fake/Scripted，0次真实Provider调用，历史数据来自真实Stage A/B。\n\n"
                    )
                    output.with_suffix(".md").write_text(
                        prefix + render_markdown(outcome.report)
                    )
        finally:
            try:
                await runtime.drain()
            finally:
                for client in clients:
                    client.close()
    print(runtime.last_outcome.metrics.model_dump_json(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["a", "b", "c"], required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--case-id", default="planner-smoke-01")
    parser.add_argument("--worker-results", type=Path, required=True)
    parser.add_argument("--review-results", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--prior-model-calls", type=int, required=True)
    parser.add_argument("--prior-tool-calls", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    plan, _ = load_worker_tasks(args.plan, args.case_id)
    worker_bytes = args.worker_results.read_bytes()
    review_document = json.loads(args.review_results.read_text())
    if (
        review_document.get("source_worker_report_sha256")
        != hashlib.sha256(worker_bytes).hexdigest()
    ):
        raise ValueError("Worker and Reviewer source reports do not match")
    workers = [
        WorkerResult.model_validate(w)
        for w in json.loads(worker_bytes)["worker_results"]
    ]
    reviews = [ReviewResult.model_validate(r) for r in review_document["results"]]
    corpus = load_paper_corpus(ROOT / "data/papers")
    resolver = EvidenceResolver(corpus, paper_catalog=load_paper_catalog())
    workers, reviews, feedback = controlled_inputs(workers, reviews, args.mode)
    prior = RecoveryBudget(
        model_calls=args.prior_model_calls, tool_calls=args.prior_tool_calls
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.validate_only:
        prepared = {
            e.paper_id for e in [*corpus.text_evidence, *corpus.figure_evidence]
        }
        decisions = RecoveryEligibilityEvaluator(resolver, prepared).evaluate(
            plan, workers, reviews, feedback
        )
        args.output.write_text(
            json.dumps(
                {
                    "mode": "validated_not_executed",
                    "real_provider_calls": 0,
                    "eligibility": [d.model_dump(mode="json") for d in decisions],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(
            "Validated Recovery eligibility; no Provider or Embedding initialization."
        )
        return
    if args.mode == "a":
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=True)
    asyncio.run(
        run_smoke(
            plan,
            workers,
            reviews,
            feedback,
            resolver,
            corpus,
            args.mode,
            prior,
            args.output,
        )
    )


if __name__ == "__main__":
    main()
