"""新版协议入口的最小装配；复用默认研究组件，不复制 Worker 循环或审核规则。"""

import asyncio
from time import perf_counter

from app.evidence.resolver import EvidenceResolver
from app.papers.catalog import load_paper_catalog
from app.papers.evidence_access import DiscoveryEvidenceRepository
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.models import ResearchPlan
from app.research.orchestrator import AsyncResearchOrchestrator
from app.research.recovery_models import (
    RecoveryBudget,
    RecoveryConfig,
    RecoveryFeedback,
    claim_ref,
)
from app.research.recovery_orchestrator import RecoveryOrchestrator
from app.research.requirements import expand_requirements, group_worker_tasks
from app.research.review_runtime import EvidenceReviewRuntime, ReviewRuntimeConfig
from app.research.review_validation import DeterministicReviewValidator
from app.research.reviewer import EvidenceReviewer
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.synthesis_agent import SynthesisAgent
from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_runtime import SynthesisRuntime
from app.research.worker import PaperResearchWorker
from app.research.worker_protocol import WorkerProtocolAdapter
from app.tools.discovery import DiscoveryScopedTextTool
from app.tools.executor import ToolExecutor
from app.tools.figure import AnalyzeFigureTool
from app.tools.paper import FindFigureTool, ReadPageTool
from app.tools.redaction import public_value
from app.tools.registry import ToolRegistry
from app.vision.analysis import FigureAnalyzer
from eval.cross_paper import research_metrics


def observable(value):
    """仅过滤导出副本中的隐藏推理字段，保留领域事实与公开执行数据。"""
    if isinstance(value, dict):
        return {
            k: observable(v)
            for k, v in value.items()
            if k.lower()
            not in {
                "reasoning",
                "reasoning_content",
                "chain_of_thought",
                "internal_thought",
                "thought",
            }
        }
    if isinstance(value, list):
        return [observable(v) for v in value]
    return value


async def run_protocol_case(
    plan, corpus, snapshot, config, retriever, client_factory, vision_factory=None
):
    """只以显式依赖组合原研究链；client_factory接收公开角色名。"""
    plan = ResearchPlan.model_validate(plan.model_dump())
    runtime_config = ResearchRuntimeConfig(**config["runtime"])
    review_config = ReviewRuntimeConfig(**config["review_runtime"])
    recovery_config = RecoveryConfig(**config["recovery"])
    if (
        len(plan.paper_ids)
        * (
            runtime_config.max_model_calls_per_worker
            + len(plan.dimensions) * review_config.max_model_calls_per_review
        )
        + 5
        > recovery_config.max_research_model_calls
    ):
        raise ValueError("Initial worst-case budget does not reserve synthesis")
    gate = ResourceGate(runtime_config)
    catalog = load_paper_catalog()
    resolver = EvidenceResolver(corpus, paper_catalog=catalog)
    repository = DiscoveryEvidenceRepository(corpus, catalog)
    clients, workers, visions = [], [], []
    started = perf_counter()

    def client(role):
        created = client_factory(role)
        clients.append((role, created))
        return created

    def worker_factory(task, recovery_context=None):
        registry = ToolRegistry()
        registry.register(ReadPageTool(repository))
        registry.register(FindFigureTool(repository))
        registry.register(DiscoveryScopedTextTool(retriever, repository, catalog))
        if vision_factory is not None:
            from app.api.runtime import CountingVisionClient

            vision = vision_factory()
            counted = CountingVisionClient(vision)
            visions.append((vision, counted))
            registry.register(AnalyzeFigureTool(resolver, FigureAnalyzer(counted)))
        worker = PaperResearchWorker(
            WorkerProtocolAdapter(
                AsyncLLMAdapter(
                    client("recovery_worker" if recovery_context else "worker"), gate
                )
            ),
            AsyncToolExecutorAdapter(ToolExecutor(registry), gate),
            resolver,
            runtime_config,
            recovery_context=recovery_context,
        )
        worker.llm.action_source = lambda: (
            worker.state.actions[-1] if worker.state and worker.state.actions else None
        )
        workers.append(worker)
        return worker

    reviews = EvidenceReviewRuntime(
        lambda task: EvidenceReviewer(
            AsyncLLMAdapter(client("reviewer"), gate),
            max_model_calls=review_config.max_model_calls_per_review,
        ),
        DeterministicReviewValidator(resolver),
        review_config,
        gate,
    )
    synthesis = SynthesisRuntime(
        SynthesisInputBuilder(resolver),
        SynthesisAgent(AsyncLLMAdapter(client("synthesis"), gate)),
    )
    pool = AsyncResearchOrchestrator(worker_factory, runtime_config, gate)
    recovery = RecoveryOrchestrator(
        lambda task: worker_factory(task.worker_task(), task),
        reviews,
        resolver,
        {e.paper_id for e in corpus.text_evidence + corpus.figure_evidence},
        config=recovery_config,
        synthesis_runtime=synthesis,
    )
    initial_workers, initial_reviews, outcome = [], [], None

    def snapshot_result(status, error_type=None):
        current_workers = initial_workers or pool.last_results
        current_reviews = initial_reviews or reviews.last_results
        current_outcome = outcome or recovery.last_outcome
        result = {
            "status": status,
            "issue_type": error_type,
            "worker_class": "PaperResearchWorker",
            "worker_classes_observed": [type(w).__name__ for w in workers],
            "coverage_worker_enabled": False,
            "plan": plan.model_dump(mode="json"),
            "initial_worker_results": [
                w.model_dump(mode="json") for w in current_workers
            ],
            "initial_review_results": [
                r.model_dump(mode="json") for r in current_reviews
            ],
            "outcome": current_outcome.model_dump(mode="json")
            if current_outcome
            else None,
            "llm_provider_attempts_observed": sum(len(c.calls) for _, c in clients),
            "vlm_client_calls_observed": sum(c.calls for _, c in visions),
            "provider_events": [
                {"role": role, "events": c.calls} for role, c in clients
            ],
            "protocol_feedback": [
                {"paper_id": w.state.paper_id, "events": w.llm.feedback_events}
                for w in workers
                if w.state
            ],
            "resource_stats": gate.stats,
            "wall_time_ms": (perf_counter() - started) * 1000,
        }
        if current_outcome is not None and current_outcome.report is not None:
            result["metrics"] = research_metrics(
                current_outcome,
                initial_workers=current_workers,
                initial_reviews=current_reviews,
                wall_time_ms=result["wall_time_ms"],
                corpus_report=snapshot,
            )
        return public_value(observable(result))

    try:
        initial_workers = await pool.run(group_worker_tasks(expand_requirements(plan)))
        initial_reviews = await reviews.run(initial_workers)
        lookup = {r.requirement_id: r for w in initial_workers for r in w.requirements}
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
            plan, initial_workers, initial_reviews, feedback, prior_usage=prior
        )
        return snapshot_result("completed")
    except asyncio.CancelledError as exc:
        exc.evaluation_snapshot = snapshot_result("cancelled", "CancelledError")
        raise
    except Exception as exc:  # noqa: BLE001 - 入口保留失败快照，不放宽领域规则
        return snapshot_result("execution_failed", type(exc).__name__)
    finally:
        try:
            await asyncio.gather(
                *(w.drain() for w in workers),
                reviews.drain(),
                synthesis.drain(),
                recovery.drain(),
            )
        finally:
            for _, created in clients:
                created.close()
            for created, _ in visions:
                created.close()
