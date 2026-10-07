"""通过事件屏障验证池级并发、故障隔离和取消，不依赖真实模型或定时等待。"""

import asyncio

import pytest
from pydantic import ValidationError

from app.research.models import (
    EvidenceRequirement,
    RequirementStatus,
    WorkerError,
    WorkerResult,
    WorkerStatus,
    WorkerTask,
    WorkerTrace,
)
from app.research.orchestrator import (
    AsyncResearchOrchestrator,
    worker_result_from_task,
)
from app.research.runtime import ResearchRuntimeConfig, ResourceGate


def make_task(index=0, requirement_count=1):
    paper = f"paper-{index}"
    return WorkerTask(
        worker_task_id=f"worker-{index}",
        paper_id=paper,
        requirements=[
            EvidenceRequirement(
                requirement_id=f"req-{index}-{j}",
                paper_id=paper,
                dimension_id=f"axis_{j}",
                question=f"What evidence addresses research axis {j}?",
            )
            for j in range(requirement_count)
        ],
    )


def resolved_requirement(requirement, *, supported=False):
    return requirement.model_copy(
        update={
            "status": RequirementStatus.SUPPORTED
            if supported
            else RequirementStatus.INSUFFICIENT_EVIDENCE,
            "claim": "Synthetic claim from observed evidence" if supported else None,
            "evidence_ids": ["ev-test"] if supported else [],
        }
    )


def completed_result(task):
    return WorkerResult(
        worker_task_id=task.worker_task_id,
        paper_id=task.paper_id,
        status=WorkerStatus.COMPLETED,
        requirements=[resolved_requirement(r) for r in task.requirements],
        attempts=1,
    )


def test_ten_workers_really_overlap_and_never_exceed_three_slots():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        started = []

        class Worker:
            async def run(self, task):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                started.append(task.worker_task_id)
                if len(started) == 3:
                    entered.set()
                try:
                    await release.wait()
                    return completed_result(task)
                finally:
                    active -= 1

        subject = AsyncResearchOrchestrator(
            lambda task: Worker(),
            ResearchRuntimeConfig(max_concurrent_workers=3),
        )
        tasks = [make_task(i) for i in range(10)]
        run = asyncio.create_task(subject.run(tasks))
        try:
            # 若实现错误地顺序 await，这个事件永远无法由三个 Worker 同时触发。
            await asyncio.wait_for(entered.wait(), 1)
            assert active == len(started) == 3
            assert not run.done()
        finally:
            release.set()
        results = await run
        assert peak == subject.trace.peak_worker_concurrency == 3
        assert len(started) == 10 and active == 0
        assert [r.worker_task_id for r in results] == [t.worker_task_id for t in tasks]
        assert all(r.status == "completed" for r in results)
        assert subject.trace.completed_count == 10
        assert subject.trace.worker_success_rate == 1

    asyncio.run(scenario())


def test_worker_exception_does_not_discard_other_results():
    class Worker:
        async def run(self, task):
            if task.paper_id == "paper-1":
                raise RuntimeError("private /home/local/path sk-private-key")
            return completed_result(task)

    subject = AsyncResearchOrchestrator(lambda task: Worker(), ResearchRuntimeConfig())
    results = asyncio.run(subject.run([make_task(i) for i in range(4)]))
    assert [r.status for r in results] == [
        "completed",
        "failed",
        "completed",
        "completed",
    ]
    assert results[1].requirements[0].status == "execution_failed"
    assert subject.trace.completed_count == 3 and subject.trace.failed_count == 1
    assert subject.trace.worker_success_rate == 0.75
    assert subject.trace.partial_failure_rate == 0.25
    for result in results:
        serialized = result.model_dump_json()
        assert "sk-private-key" not in serialized and "/home/local" not in serialized


def test_cancelling_orchestrator_propagates_and_preserves_ordered_snapshots():
    async def scenario():
        entered = asyncio.Event()
        never_released = asyncio.Event()
        cancellation_seen = []

        class Worker:
            last_result = None

            async def run(self, task):
                current = [resolved_requirement(task.requirements[0], supported=True)]
                current += task.requirements[1:]
                entered.set()
                try:
                    await never_released.wait()
                except asyncio.CancelledError:
                    cancellation_seen.append(task.worker_task_id)
                    self.last_result = worker_result_from_task(
                        task,
                        current_requirements=current,
                        unresolved_status=RequirementStatus.CANCELLED,
                    )
                    raise

        subject = AsyncResearchOrchestrator(
            lambda task: Worker(),
            ResearchRuntimeConfig(max_concurrent_workers=1),
        )
        tasks = [make_task(i, 2) for i in range(3)]
        run = asyncio.create_task(subject.run(tasks))
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert cancellation_seen == [tasks[0].worker_task_id]
        assert [r.worker_task_id for r in subject.last_results] == [
            t.worker_task_id for t in tasks
        ]
        assert all(r.status == "cancelled" for r in subject.last_results)
        first, *queued = subject.last_results
        assert [r.status for r in first.requirements] == ["supported", "cancelled"]
        assert all(r.attempts == 0 for r in queued)
        assert all(
            requirement.status == "cancelled"
            for result in queued
            for requirement in result.requirements
        )
        assert subject.trace.cancelled_count == 3
        assert subject.trace.resolution_rate == subject.trace.coverage == 1 / 6

    asyncio.run(scenario())


def test_partial_and_insufficient_have_different_pool_metrics():
    class Worker:
        async def run(self, task):
            if task.paper_id == "paper-0":
                return completed_result(task)
            if task.paper_id == "paper-1":
                return worker_result_from_task(
                    task,
                    current_requirements=[
                        resolved_requirement(task.requirements[0], supported=True),
                        task.requirements[1],
                    ],
                    error=WorkerError(code="budget_exhausted", message="Budget ended."),
                )
            return worker_result_from_task(task)

    tasks = [make_task(0), make_task(1, 2), make_task(2, 2)]
    subject = AsyncResearchOrchestrator(
        lambda task: Worker(), ResearchRuntimeConfig(), clock=lambda: 12.0
    )
    results = asyncio.run(subject.run(tasks))
    assert [r.status for r in results] == ["completed", "partial_success", "failed"]
    assert results[0].requirements[0].status == "insufficient_evidence"
    assert results[2].requirements[0].status == "execution_failed"
    assert subject.trace.resolution_rate == 2 / 5
    assert subject.trace.coverage == 1 / 5
    assert subject.trace.worker_success_rate == 1 / 3
    assert subject.trace.partial_failure_rate == 2 / 3
    assert subject.trace.total_latency_ms == 0


def test_empty_task_list_and_duplicate_worker_identity():
    subject = AsyncResearchOrchestrator(lambda task: None, ResearchRuntimeConfig())
    assert asyncio.run(subject.run([])) == []
    assert subject.trace.worker_task_count == 0
    with pytest.raises(ValueError):
        asyncio.run(subject.run([make_task(), make_task()]))


def test_result_failure_helper_preserves_supported_and_stable_identity():
    task = make_task(2, 2)
    current = [resolved_requirement(task.requirements[0], supported=True)]
    result = worker_result_from_task(
        task,
        current_requirements=current + task.requirements[1:],
        error=WorkerError(code="budget_exhausted", message="Budget ended."),
    )
    assert result.worker_task_id == task.worker_task_id
    assert result.paper_id == task.paper_id
    assert [r.requirement_id for r in result.requirements] == [
        r.requirement_id for r in task.requirements
    ]
    assert result.status == "partial_success"
    assert [r.status for r in result.requirements] == ["supported", "execution_failed"]


@pytest.mark.parametrize(
    "status,requirement_status",
    [
        ("completed", "execution_failed"),
        ("partial_success", "insufficient_evidence"),
        ("failed", "insufficient_evidence"),
        ("cancelled", "execution_failed"),
        ("completed", "pending"),
    ],
)
def test_worker_result_rejects_inconsistent_status(status, requirement_status):
    task = make_task()
    with pytest.raises(ValidationError):
        WorkerResult(
            worker_task_id=task.worker_task_id,
            paper_id=task.paper_id,
            status=status,
            requirements=[
                task.requirements[0].model_copy(update={"status": requirement_status})
            ],
        )


def test_pool_reports_worker_and_resource_peaks_independently():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        config = ResearchRuntimeConfig(max_concurrent_workers=3, max_concurrent_llm=1)
        gate = ResourceGate(config)
        starts = []

        class Worker:
            async def run(self, task):
                starts.append(task.worker_task_id)
                if len(starts) == 3:
                    entered.set()
                async with gate.slot("llm"):
                    await release.wait()
                return completed_result(task)

        subject = AsyncResearchOrchestrator(lambda task: Worker(), config, gate)
        run = asyncio.create_task(subject.run([make_task(i) for i in range(3)]))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert len(starts) == 3 and gate.stats["llm"]["active"] == 1
        finally:
            release.set()
        await run
        assert subject.trace.peak_worker_concurrency == 3
        assert subject.trace.peak_llm_concurrency == 1
        assert subject.trace.peak_vlm_concurrency == 0
        assert subject.trace.peak_retrieval_concurrency == 0

    asyncio.run(scenario())


def test_retry_rate_counts_retried_logical_dependencies_not_physical_attempts():
    class Worker:
        async def run(self, task):
            result = completed_result(task)
            trace = WorkerTrace(
                worker_task_id=task.worker_task_id,
                paper_id=task.paper_id,
                status="completed",
                requirement_count=1,
                resolved_count=1,
                model_calls=4,
                tool_calls=1,
                dependency_calls=3,
                retried_dependency_calls=1,
                retry_count=2,
            )
            return WorkerResult.model_validate(result.model_dump() | {"trace": trace})

    subject = AsyncResearchOrchestrator(lambda task: Worker(), ResearchRuntimeConfig())
    asyncio.run(subject.run([make_task()]))
    assert subject.trace.dependency_calls == 3
    assert subject.trace.retried_dependency_calls == 1
    assert subject.trace.retry_count == 2
    assert subject.trace.retry_rate == 1 / 3
