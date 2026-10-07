"""以有界并发执行逐篇研究任务；隔离普通失败并向下传播取消。"""

import asyncio
from collections.abc import Callable
from time import perf_counter
from typing import Protocol

from app.research.models import (
    EvidenceRequirement,
    OrchestratorTrace,
    RequirementStatus,
    WorkerError,
    WorkerResult,
    WorkerStatus,
    WorkerTask,
    WorkerTrace,
)
from app.research.requirements import research_completion
from app.research.runtime import ResearchRuntimeConfig, ResourceGate


class WorkerRunner(Protocol):
    async def run(self, task: WorkerTask) -> WorkerResult: ...


def _validate_scope(task: WorkerTask, result: WorkerResult) -> WorkerResult:
    """结果只能更新状态和证据，不得丢失或改写计划中的研究需求。"""
    if not isinstance(result, WorkerResult):
        raise TypeError("Worker must return a typed WorkerResult")
    if result.worker_task_id != task.worker_task_id or result.paper_id != task.paper_id:
        raise ValueError("Worker result has the wrong task identity")
    source = {item.requirement_id: item for item in task.requirements}
    returned = {item.requirement_id: item for item in result.requirements}
    if source.keys() != returned.keys():
        raise ValueError("Worker result must retain every original requirement")
    for identity, item in returned.items():
        original = source[identity]
        if (
            item.dimension_id != original.dimension_id
            or item.question != original.question
            or item.required != original.required
        ):
            raise ValueError("Worker result changed a research requirement")
    return result


def worker_result_from_task(
    task: WorkerTask,
    *,
    unresolved_status: RequirementStatus = RequirementStatus.EXECUTION_FAILED,
    error: WorkerError | None = None,
    current_requirements: list[EvidenceRequirement] | None = None,
    attempts: int = 1,
    latency_ms: float = 0,
    trace: WorkerTrace | None = None,
) -> WorkerResult:
    """故障或取消时保存已有研究进度，只将 pending 转为终态。"""
    if unresolved_status not in (
        RequirementStatus.EXECUTION_FAILED,
        RequirementStatus.CANCELLED,
    ):
        raise ValueError("Fallback must represent execution failure or cancellation")
    current = (
        task.requirements if current_requirements is None else current_requirements
    )
    requirements = [
        EvidenceRequirement.model_validate(
            {
                **item.model_dump(),
                "status": unresolved_status,
                "claim": None,
                "evidence_ids": [],
            }
        )
        if item.status == RequirementStatus.PENDING
        else item
        for item in current
    ]
    resolved = sum(
        item.status
        in (RequirementStatus.SUPPORTED, RequirementStatus.INSUFFICIENT_EVIDENCE)
        for item in requirements
    )
    if resolved == len(requirements):
        status = WorkerStatus.COMPLETED
        error = None
    elif unresolved_status == RequirementStatus.CANCELLED and any(
        item.status == RequirementStatus.CANCELLED for item in requirements
    ):
        status = WorkerStatus.CANCELLED
    elif resolved:
        status = WorkerStatus.PARTIAL_SUCCESS
    else:
        status = WorkerStatus.FAILED
    if trace is not None:
        trace = WorkerTrace.model_validate(
            {
                **trace.model_dump(),
                "worker_task_id": task.worker_task_id,
                "paper_id": task.paper_id,
                "requirement_count": len(requirements),
                "resolved_count": resolved,
                "status": status,
                "latency_ms": latency_ms,
            }
        )
    return _validate_scope(
        task,
        WorkerResult(
            worker_task_id=task.worker_task_id,
            paper_id=task.paper_id,
            status=status,
            requirements=requirements,
            error=error,
            attempts=attempts,
            latency_ms=latency_ms,
            trace=trace,
        ),
    )


class AsyncResearchOrchestrator:
    """Worker 槽位与共享资源槽位分别限制；不会因单篇失败丢失其他结果。"""

    def __init__(
        self,
        worker_factory: Callable[[WorkerTask], WorkerRunner],
        config: ResearchRuntimeConfig,
        resource_gate: ResourceGate | None = None,
        *,
        clock: Callable[[], float] = perf_counter,
    ):
        self.worker_factory = worker_factory
        self.config = config
        self.resource_gate = resource_gate or ResourceGate(config)
        self.clock = clock
        self.last_results: list[WorkerResult] = []
        self.trace: OrchestratorTrace | None = None
        self._running = False
        self._active = 0
        self._peak = 0

    async def run(self, tasks: list[WorkerTask]) -> list[WorkerResult]:
        """并发执行并保持输入顺序；取消时先保存快照，再重新抛出取消。"""
        if self._running:
            raise RuntimeError("Orchestrator already has an active request")
        identities = [task.worker_task_id for task in tasks]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate worker_task_id")
        papers = [task.paper_id for task in tasks]
        if len(set(papers)) != len(papers):
            raise ValueError("Each paper must have exactly one worker task")
        requirement_ids = [
            item.requirement_id for task in tasks for item in task.requirements
        ]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("Duplicate requirement_id across worker tasks")
        self._running = True
        self._active = self._peak = 0
        self.last_results = []
        self.trace = None
        started = self.clock()
        semaphore = asyncio.Semaphore(self.config.max_concurrent_workers)
        slots: list[WorkerResult | None] = [None] * len(tasks)
        children = [
            asyncio.create_task(self._run_one(task, index, slots, semaphore))
            for index, task in enumerate(tasks)
        ]
        try:
            await asyncio.gather(*children)
        except asyncio.CancelledError:
            for child in children:
                child.cancel()
            # 收集清理结果但不将取消当成普通成功返回。
            await asyncio.gather(*children, return_exceptions=True)
            for index, task in enumerate(tasks):
                if slots[index] is None:
                    slots[index] = self._cancelled(task, attempts=0, latency_ms=0)
            raise
        finally:
            self.last_results = [result for result in slots if result is not None]
            self._record_trace(started, len(tasks))
            self._running = False
        return list(self.last_results)

    async def _run_one(self, task, index, slots, semaphore):
        worker = None
        acquired = False
        started = self.clock()
        try:
            async with semaphore:
                acquired = True
                self._active += 1
                self._peak = max(self._peak, self._active)
                started = self.clock()
                try:
                    worker = self.worker_factory(task)
                    result = await worker.run(task)
                    slots[index] = _validate_scope(task, result)
                finally:
                    self._active -= 1
        except asyncio.CancelledError:
            saved = getattr(worker, "last_result", None)
            try:
                slots[index] = _validate_scope(task, saved)
            except (ValueError, TypeError):
                slots[index] = self._cancelled(
                    task,
                    attempts=int(acquired),
                    latency_ms=max(0, (self.clock() - started) * 1000)
                    if acquired
                    else 0,
                )
            raise
        except Exception:  # noqa: BLE001 -- 池边界必须隔离未知 Worker 异常；取消已单独传播。
            # 异常正文可能带有凭据或路径，公开结果只保留稳定错误码。
            slots[index] = worker_result_from_task(
                task,
                error=WorkerError(
                    code="worker_execution_failure",
                    message="Worker execution failed; other paper tasks remain available.",
                    requirement_ids=[item.requirement_id for item in task.requirements],
                ),
                attempts=int(acquired),
                latency_ms=max(0, (self.clock() - started) * 1000),
            )

    @staticmethod
    def _cancelled(task, *, attempts, latency_ms):
        return worker_result_from_task(
            task,
            unresolved_status=RequirementStatus.CANCELLED,
            error=WorkerError(
                code="cancelled",
                message="Research execution was cancelled by the caller.",
                requirement_ids=[item.requirement_id for item in task.requirements],
            ),
            attempts=attempts,
            latency_ms=latency_ms,
        )

    def _record_trace(self, started, task_count):
        counts = {status: 0 for status in WorkerStatus}
        for result in self.last_results:
            counts[result.status] += 1
        requirements = [
            item for result in self.last_results for item in result.requirements
        ]
        completion = research_completion(requirements)
        dependency_calls = sum(
            result.trace.dependency_calls
            for result in self.last_results
            if result.trace is not None
        )
        retried_calls = sum(
            result.trace.retried_dependency_calls
            for result in self.last_results
            if result.trace is not None
        )
        retry_count = sum(
            result.trace.retry_count
            for result in self.last_results
            if result.trace is not None
        )
        peaks = self.resource_gate.peaks
        self.trace = OrchestratorTrace(
            worker_task_count=task_count,
            completed_count=counts[WorkerStatus.COMPLETED],
            partial_success_count=counts[WorkerStatus.PARTIAL_SUCCESS],
            failed_count=counts[WorkerStatus.FAILED],
            cancelled_count=counts[WorkerStatus.CANCELLED],
            peak_worker_concurrency=self._peak,
            peak_llm_concurrency=peaks["llm"],
            peak_vlm_concurrency=peaks["vlm"],
            peak_retrieval_concurrency=peaks["retrieval"],
            total_latency_ms=max(0, (self.clock() - started) * 1000),
            resolution_rate=completion.resolution_rate,
            coverage=completion.coverage,
            worker_success_rate=counts[WorkerStatus.COMPLETED] / task_count
            if task_count
            else 0,
            partial_failure_rate=(
                counts[WorkerStatus.PARTIAL_SUCCESS] + counts[WorkerStatus.FAILED]
            )
            / task_count
            if task_count
            else 0,
            retry_rate=retried_calls / dependency_calls if dependency_calls else 0,
            dependency_calls=dependency_calls,
            retried_dependency_calls=retried_calls,
            retry_count=retry_count,
        )
