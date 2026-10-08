"""独立 F3.1-E 实验接线；不替换冻结入口，不读取答案或语义评分。"""

import asyncio
from copy import deepcopy
from time import perf_counter

from app.evidence.resolver import EvidenceResolver
from app.papers.catalog import load_paper_catalog
from app.papers.evidence_access import DiscoveryEvidenceRepository
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.coverage_worker import CoverageAwarePaperWorker
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
from app.tools.discovery import DiscoveryScopedTextTool
from app.tools.executor import ToolExecutor
from app.tools.figure import AnalyzeFigureTool
from app.tools.paper import FindFigureTool, ReadPageTool
from app.tools.redaction import public_value
from app.tools.registry import ToolRegistry
from app.vision.analysis import FigureAnalyzer
from eval.cross_paper import research_metrics
from eval.research_quality.coverage_comparison import (
    CoverageComparisonRequest,
    build_comparison_plan,
)


def _observable_value(value):
    """排除可辨识的隐藏推理字段；只改导出副本，不影响实际工具执行。"""
    hidden = {"reasoning", "chain_of_thought", "internal_thought", "thought"}
    if isinstance(value, dict):
        return {
            key: _observable_value(item)
            for key, item in value.items()
            if key.lower() not in hidden
        }
    if isinstance(value, list):
        return [_observable_value(item) for item in value]
    return value


class RecordingVisionClient:
    """透明组合原 VLM；只记录 analyze 边界，不宣称内部 SDK 精确账单。"""

    def __init__(self, client):
        self.client, self.calls = client, []
        self.model = getattr(client, "model", None)

    def analyze(self, image_path, prompt):
        started = perf_counter()
        event = {
            "status": "error",
            "counting_boundary": "vision_client_analyze_not_sdk_request",
        }
        try:
            result = self.client.analyze(image_path, prompt)
            event["status"] = "success"
            return result
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["latency_ms"] = (perf_counter() - started) * 1000
            self.calls.append(event)

    def close(self):
        close = getattr(self.client, "close", None)
        if close is not None:
            close()


class RecordingToolExecutor:
    """只旁录真实执行与完整脱敏 Observation；不增加请求、重试或工具。"""

    def __init__(self, executor, records, *, worker_task_id, paper_id, phase):
        self.executor, self.records = executor, records
        self.identity = {
            "worker_task_id": worker_task_id,
            "paper_id": paper_id,
            "phase": phase,
        }
        self.attempts = 0

    @property
    def registry(self):
        return self.executor.registry

    @property
    def traces(self):
        return self.executor.traces

    def execute(self, name, arguments, *, tool_call_id=None):
        started, previous = perf_counter(), len(self.executor.traces)
        self.attempts += 1
        record = self.identity | {
            "physical_tool_attempt": self.attempts,
            "requested_tool_call_id": tool_call_id,
            "tool_name": name,
            "arguments": public_value(deepcopy(arguments)),
        }
        try:
            result = self.executor.execute(name, arguments, tool_call_id=tool_call_id)
            record["observation"] = public_value(result.model_dump(mode="json"))
            return result
        except Exception as exc:
            record["exception_type"] = type(exc).__name__
            raise
        finally:
            record["elapsed_ms"] = (perf_counter() - started) * 1000
            new_traces = self.executor.traces[previous:]
            record["execution_traces"] = [
                public_value(item.model_dump(mode="json")) for item in new_traces
            ]
            self.records.append(_observable_value(public_value(record)))


def _provider_events(clients):
    """只读取 Provider 已保存的公开动作事实；Fake 的 messages 也不会被导出。"""
    rows = []
    allowed = (
        "status",
        "tool_calls",
        "finish_reason",
        "failure",
        "error_type",
        "http_status",
        "latency_ms",
        "counting_boundary",
    )
    for client, metadata in clients:
        for index, event in enumerate(getattr(client, "calls", ()), start=1):
            if isinstance(event, dict):
                rows.append(
                    _observable_value(
                        public_value(
                            metadata
                            | {"attempt": index}
                            | {
                                key: deepcopy(event[key])
                                for key in allowed
                                if key in event
                            }
                        )
                    )
                )
    return rows


async def run_coverage_case(
    request,
    corpus,
    snapshot,
    config,
    *,
    retriever,
    client_factory,
    vision_client_factory=None,
    real_provider_used=True,
    catalog=None,
):
    """显式运行覆盖 Worker 全链路，返回可与 F2 detail 对照的记录。

    client_factory(role, task) 接收 worker/recovery_worker/reviewer/synthesis 角色，
    task 分别为 WorkerTask、ReviewTask 或 None。调用方负责实例化原 Provider。
    vision_client_factory(task) 可注入原 VLM；真实运行必须提供，Fake 可省略。
    只消费白名单请求；原预测、参考答案和评测备注不属于该接口。
    """
    request = CoverageComparisonRequest.model_validate(request)
    plan = build_comparison_plan(request)
    configuration = deepcopy(config)
    runtime_config = ResearchRuntimeConfig(**configuration["runtime"])
    review_config = ReviewRuntimeConfig(**configuration["review_runtime"])
    recovery_config = RecoveryConfig(**configuration["recovery"])
    if real_provider_used and vision_client_factory is None:
        raise ValueError("real_execution_requires_original_vision_client_factory")
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
    catalog = load_paper_catalog() if catalog is None else catalog
    resolver = EvidenceResolver(corpus, paper_catalog=catalog)
    repository = DiscoveryEvidenceRepository(corpus, catalog)
    clients, workers, observations, feedback, cleanup_errors = [], [], [], [], []
    initial_workers, initial_reviews, outcome = [], [], None
    started = perf_counter()

    def make_client(role, task):
        client = client_factory(role, task)
        clients.append(
            (
                client,
                {
                    "resource": "llm",
                    "role": role,
                    "paper_id": getattr(task, "paper_id", None)
                    or getattr(getattr(task, "requirement", None), "paper_id", None),
                    "task_id": getattr(task, "worker_task_id", None),
                    "model": getattr(client, "model", None),
                },
            )
        )
        return client

    def worker_factory(task, recovery_context=None):
        phase = "recovery" if recovery_context is not None else "initial"
        registry = ToolRegistry()
        registry.register(ReadPageTool(repository))
        registry.register(FindFigureTool(repository))
        registry.register(DiscoveryScopedTextTool(retriever, repository, catalog))
        if vision_client_factory is not None:
            vision = RecordingVisionClient(vision_client_factory(task))
            clients.append(
                (
                    vision,
                    {
                        "resource": "vlm",
                        "role": "figure_analysis",
                        "phase": phase,
                        "paper_id": task.paper_id,
                        "task_id": task.worker_task_id,
                        "model": getattr(vision, "model", None),
                    },
                )
            )
            registry.register(AnalyzeFigureTool(resolver, FigureAnalyzer(vision)))
        recorder = RecordingToolExecutor(
            ToolExecutor(registry),
            observations,
            worker_task_id=task.worker_task_id,
            paper_id=task.paper_id,
            phase=phase,
        )
        worker = CoverageAwarePaperWorker(
            AsyncLLMAdapter(
                make_client("recovery_worker" if recovery_context else "worker", task),
                gate,
            ),
            AsyncToolExecutorAdapter(recorder, gate),
            resolver,
            runtime_config,
            recovery_context=recovery_context,
        )
        workers.append((worker, phase, task))
        return worker

    review_runtime = EvidenceReviewRuntime(
        lambda task: EvidenceReviewer(
            AsyncLLMAdapter(make_client("reviewer", task), gate),
            max_model_calls=review_config.max_model_calls_per_review,
        ),
        DeterministicReviewValidator(resolver),
        review_config,
        gate,
    )
    synthesis = SynthesisRuntime(
        SynthesisInputBuilder(resolver),
        SynthesisAgent(AsyncLLMAdapter(make_client("synthesis", None), gate)),
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
    failure, cancelled = None, None
    status = "completed"
    try:
        initial_workers = await pool.run(group_worker_tasks(expand_requirements(plan)))
        initial_reviews = await review_runtime.run(initial_workers)
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
    except asyncio.CancelledError as exc:
        cancelled, status = exc, "cancelled"
        failure = {"issue_type": "CancelledError"}
    except Exception as exc:  # noqa: BLE001 - 实验边界保留失败快照，不泄漏异常正文
        status, failure = "execution_failed", {"issue_type": type(exc).__name__}
    finally:
        # 与原运行器一样等待已启动线程退出；不能把取消后的后台尝试记成零。
        await asyncio.gather(
            *(worker.drain() for worker, _, _ in workers),
            review_runtime.drain(),
            synthesis.drain(),
            recovery.drain(),
            return_exceptions=True,
        )
        for client, _ in clients:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - 清理失败不能覆盖研究或取消结果
                    cleanup_errors.append({"issue_type": type(exc).__name__})
    current_workers = initial_workers or pool.last_results
    current_reviews = initial_reviews or review_runtime.last_results
    outcome = outcome or recovery.last_outcome
    events = _provider_events(clients)
    llm_attempts = sum(
        len(getattr(c, "calls", ())) for c, m in clients if m["resource"] == "llm"
    )
    vlm_attempts = sum(
        len(getattr(c, "calls", ())) for c, m in clients if m["resource"] == "vlm"
    )
    diagnostics = [
        {
            "phase": phase,
            "attempt_index": int(phase == "recovery"),
            "worker_task_id": task.worker_task_id,
            "paper_id": task.paper_id,
            "worker_class": type(worker).__name__,
            "worker_result": worker.last_result.model_dump(mode="json")
            if worker.last_result
            else None,
            "coverage": worker.last_coverage_report,
            "report": worker.last_coverage_report,
            "llm_adapter_stats": worker.llm.snapshot(),
            "tool_adapter_stats": worker.tools.snapshot(),
        }
        for worker, phase, task in workers
    ]
    detail = {
        "case_id": request.case_id,
        "status": status,
        "worker_class": "app.research.coverage_worker.CoverageAwarePaperWorker",
        "real_provider_used": real_provider_used,
        "real_provider_calls": (llm_attempts if vlm_attempts == 0 else None)
        if real_provider_used
        else 0,
        "llm_provider_attempts_observed": llm_attempts,
        "vlm_provider_attempts_observed": 0 if vlm_attempts == 0 else None,
        "vlm_client_calls_observed": vlm_attempts,
        "planner_mode": configuration.get("planner_mode"),
        "planner_model_calls": 0,
        "plan": plan.model_dump(mode="json"),
        "input_projection": request.model_dump(mode="json"),
        "configuration": configuration,
        "corpus_snapshot": deepcopy(snapshot),
        "initial_worker_results": [w.model_dump(mode="json") for w in current_workers],
        "initial_review_results": [r.model_dump(mode="json") for r in current_reviews],
        "recovery_feedback": [f.model_dump(mode="json") for f in feedback],
        "orchestrator_trace": pool.trace.model_dump(mode="json")
        if pool.trace
        else None,
        "outcome": outcome.model_dump(mode="json") if outcome else None,
        "synthesis_input": synthesis.last_input.model_dump(mode="json")
        if synthesis.last_input
        else None,
        "final_report": synthesis.last_report.model_dump(mode="json")
        if synthesis.last_report
        else None,
        "coverage_diagnostics": diagnostics,
        "tool_observations": observations,
        "provider_attempts": events,
        "resource_trace": gate.stats,
        "wall_time_ms": (perf_counter() - started) * 1000,
        "failure": failure,
        "cleanup_errors": cleanup_errors,
        "quality_result_usable": status == "completed" and outcome is not None,
        "limitations": [
            "Coverage declarations and Reviewer verdicts are not independent semantic scores.",
            "Provider attempts are observed call records, not provider billing totals.",
            "No hidden reasoning or model memory is exported.",
        ],
    }
    if outcome is not None and outcome.report is not None:
        detail["metrics"] = research_metrics(
            outcome,
            initial_workers=current_workers,
            initial_reviews=current_reviews,
            wall_time_ms=detail["wall_time_ms"],
            corpus_report=snapshot,
        )
    detail = _observable_value(detail)
    if cancelled is not None:
        cancelled.evaluation_snapshot = detail
        raise cancelled
    return detail
