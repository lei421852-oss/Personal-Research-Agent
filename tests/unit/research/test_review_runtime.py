"""事件屏障和伪模型验证授权 Stage B 审核池；不调用真实模型或定时 sleep。"""

import asyncio
import threading

import pytest
from pydantic import ValidationError

from app.llm.models import LLMResponse
from app.research.async_tools import AsyncLLMAdapter
from app.research.models import EvidenceRequirement, WorkerResult
from app.research.retry import RetryPolicy
from app.research.review_models import (
    ReviewEvidence,
    ReviewIssue,
    ReviewResult,
    ReviewTask,
    ReviewTrace,
)
from app.research.review_runtime import EvidenceReviewRuntime, ReviewRuntimeConfig
from app.research.runtime import ResearchRuntimeConfig, ResourceGate


def make_workers(count=1, *, statuses=None):
    statuses = statuses or ["supported"] * count
    workers = []
    for index, status in enumerate(statuses):
        supported = status == "supported"
        requirement = EvidenceRequirement(
            requirement_id=f"review-req-{index}",
            paper_id=f"review-paper-{index}",
            dimension_id="test_axis",
            question="What does this prepared evidence support?",
            status=status,
            claim=f"Synthetic claim {index}." if supported else None,
            evidence_ids=[f"review-ev-{index}"] if supported else [],
        )
        workers.append(
            WorkerResult(
                worker_task_id=f"review-worker-{index}",
                paper_id=requirement.paper_id,
                status="completed"
                if status in {"supported", "insufficient_evidence"}
                else "failed",
                requirements=[requirement],
                observed_evidence_ids=requirement.evidence_ids,
            )
        )
    return workers


class FakeValidator:
    """只隔离调度连线；证据身份校验由独立 Validator 测试覆盖。"""

    def validate(self, worker, requirement):
        if requirement.status != "supported":
            return ReviewResult(
                worker_task_id=worker.worker_task_id,
                requirement_id=requirement.requirement_id,
                paper_id=requirement.paper_id,
                status="not_reviewable",
                validation_status="passed",
                skip_reason=requirement.status.value,
            )
        return ReviewTask(
            worker_task_id=worker.worker_task_id,
            requirement=requirement,
            evidence=[
                ReviewEvidence(
                    evidence_id=identity,
                    paper_id=requirement.paper_id,
                    page=1,
                    modality="text",
                    text="Prepared synthetic evidence supporting this claim.",
                )
                for identity in requirement.evidence_ids
            ],
        )


def verdict(task, *, model_calls=0, retry_count=0, rejected=False):
    return ReviewResult(
        worker_task_id=task.worker_task_id,
        requirement_id=task.requirement.requirement_id,
        paper_id=task.requirement.paper_id,
        status="rejected" if rejected else "passed",
        reason_code="evidence_does_not_support_claim"
        if rejected
        else "evidence_supports_claim",
        claim=task.requirement.claim,
        evidence_ids=task.requirement.evidence_ids,
        validation_status="passed",
        trace=ReviewTrace(model_calls=model_calls, retry_count=retry_count),
    )


def cancelled(task, *, model_calls=0):
    return ReviewResult(
        worker_task_id=task.worker_task_id,
        requirement_id=task.requirement.requirement_id,
        paper_id=task.requirement.paper_id,
        status="not_reviewable",
        claim=task.requirement.claim,
        evidence_ids=task.requirement.evidence_ids,
        validation_status="passed",
        issue=ReviewIssue(code="cancelled", message="Review was cancelled."),
        trace=ReviewTrace(model_calls=model_calls),
    )


class FakeReviewer:
    def __init__(self, gate, *, max_model_calls=3):
        self.resource_gate = gate
        self.max_model_calls = max_model_calls
        self.last_result = None

    async def run(self, task):
        return verdict(task)

    async def drain(self):
        return None


async def no_sleep(delay):
    return None


def adapter(client, gate):
    return AsyncLLMAdapter(
        client,
        gate,
        retry_policy=RetryPolicy(max_retries=2, sleeper=no_sleep, jitter=lambda: 0),
    )


def test_ten_requirements_overlap_under_three_fixed_review_consumers(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        active = peak = 0
        started, consumer_tasks = [], []
        create_task = asyncio.create_task
        gate = ResourceGate()

        def track_consumer(coroutine, *args, **kwargs):
            # 外层 run 使用保存的原函数启动；这里只数池内实际分配的 Task。
            consumer_tasks.append(coroutine)
            return create_task(coroutine, *args, **kwargs)

        monkeypatch.setattr(asyncio, "create_task", track_consumer)

        class Reviewer(FakeReviewer):
            async def run(self, task):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                started.append(task.requirement.requirement_id)
                if len(started) == 3:
                    entered.set()
                try:
                    await release.wait()
                    return verdict(task)
                finally:
                    active -= 1

        subject = EvidenceReviewRuntime(
            lambda task: Reviewer(gate),
            FakeValidator(),
            ReviewRuntimeConfig(max_concurrent_reviewers=3),
            gate,
        )
        workers = make_workers(10)
        before = [worker.model_dump() for worker in workers]
        run = create_task(subject.run(workers))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert len(started) == active == 3 and not run.done()
        finally:
            release.set()
        results = await run
        assert len(consumer_tasks) == 3
        assert peak == subject.trace.peak_reviewer_concurrency == 3
        assert len(started) == 10 and active == 0
        assert [result.requirement_id for result in results] == [
            worker.requirements[0].requirement_id for worker in workers
        ]
        assert all(result.status == "passed" for result in results)
        assert subject.trace.reviewed_count == subject.trace.passed_count == 10
        assert [worker.model_dump() for worker in workers] == before

    asyncio.run(scenario())


def test_native_async_llm_gate_is_independent_of_reviewer_limit(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        reviewers_started = []
        client_active = client_peak = 0
        gate = ResourceGate(ResearchRuntimeConfig(max_concurrent_llm=1))

        async def forbidden_thread(*args, **kwargs):
            raise AssertionError("Native async dependency must not enter to_thread")

        monkeypatch.setattr(asyncio, "to_thread", forbidden_thread)

        class Client:
            async def complete(self, *, messages, tools):
                nonlocal client_active, client_peak
                client_active += 1
                client_peak = max(client_peak, client_active)
                try:
                    await release.wait()
                    return LLMResponse()
                finally:
                    client_active -= 1

        class Reviewer(FakeReviewer):
            def __init__(self):
                super().__init__(gate)
                self.llm = adapter(Client(), gate)

            async def run(self, task):
                reviewers_started.append(task.requirement.requirement_id)
                if len(reviewers_started) == 3:
                    entered.set()
                await self.llm.complete(messages=[], tools=[])
                return verdict(task, model_calls=self.llm.snapshot()["total_attempts"])

        subject = EvidenceReviewRuntime(
            lambda task: Reviewer(),
            FakeValidator(),
            ReviewRuntimeConfig(max_concurrent_reviewers=3),
            gate,
        )
        run = asyncio.create_task(subject.run(make_workers(7)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert gate.stats["llm"]["active"] == 1
        finally:
            release.set()
        results = await run
        assert all(result.status == "passed" for result in results)
        assert subject.trace.peak_reviewer_concurrency == 3
        assert client_peak == subject.trace.peak_llm_concurrency == 1
        assert subject.trace.reviewer_model_calls == 7

    asyncio.run(scenario())


def test_static_skips_and_reviewer_failure_preserve_order_and_siblings():
    gate = ResourceGate()
    factory_calls = []

    class Reviewer(FakeReviewer):
        async def run(self, task):
            if task.requirement.requirement_id == "review-req-2":
                raise RuntimeError("private /home/local/review sk-review-private-key")
            return verdict(task, rejected=task.requirement.requirement_id.endswith("3"))

    def factory(task):
        factory_calls.append(task.requirement.requirement_id)
        return Reviewer(gate)

    workers = make_workers(
        statuses=["supported", "insufficient_evidence", "supported", "supported"]
    )
    subject = EvidenceReviewRuntime(factory, FakeValidator(), resource_gate=gate)
    results = asyncio.run(subject.run(workers))
    assert [result.requirement_id for result in results] == [
        f"review-req-{index}" for index in range(4)
    ]
    assert [result.status for result in results] == [
        "passed",
        "not_reviewable",
        "not_reviewable",
        "rejected",
    ]
    assert factory_calls == ["review-req-0", "review-req-2", "review-req-3"]
    assert results[1].skip_reason == "insufficient_evidence"
    assert results[2].issue is not None and results[3].issue is None
    assert subject.trace.total_requirement_count == 4
    assert subject.trace.reviewable_requirement_count == 3
    assert subject.trace.reviewed_count == 2
    assert subject.trace.passed_count == subject.trace.rejected_count == 1
    assert subject.trace.not_reviewable_count == 2 and subject.trace.bypassed_count == 1
    serialized = "".join(result.model_dump_json() for result in results)
    assert "sk-review-private-key" not in serialized and "/home/local" not in serialized


def test_no_reviewable_requirements_never_builds_reviewer_or_model():
    def forbidden_factory(task):
        raise AssertionError("Static skips must never create a Reviewer")

    workers = make_workers(statuses=["insufficient_evidence", "execution_failed"])
    subject = EvidenceReviewRuntime(
        forbidden_factory, FakeValidator(), clock=lambda: 5.0
    )
    results = asyncio.run(subject.run(workers))
    assert all(result.status == "not_reviewable" for result in results)
    assert subject.reviewers == []
    assert subject.trace.reviewable_requirement_count == 0
    assert subject.trace.bypassed_count == subject.trace.not_reviewable_count == 2
    assert subject.trace.reviewer_model_calls == subject.trace.retry_count == 0
    assert (
        subject.trace.peak_reviewer_concurrency
        == subject.trace.peak_llm_concurrency
        == 0
    )
    assert subject.trace.total_latency_ms == 0


def test_empty_batch_has_zero_metrics_and_no_factory():
    subject = EvidenceReviewRuntime(
        lambda task: pytest.fail("Empty batch cannot build reviewers"), FakeValidator()
    )
    assert asyncio.run(subject.run([])) == []
    assert subject.trace.total_requirement_count == 0
    assert subject.trace.reviewed_count == subject.trace.reviewer_model_calls == 0


def test_reviewer_cancellation_propagates_and_keeps_completed_result():
    async def scenario():
        entered = asyncio.Event()
        client_started, client_cancelled = [], []
        gate = ResourceGate(ResearchRuntimeConfig(max_concurrent_llm=2))

        class Client:
            def __init__(self, identity):
                self.identity = identity

            async def complete(self, *, messages, tools):
                client_started.append(self.identity)
                if len(client_started) == 2:
                    entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    client_cancelled.append(self.identity)
                    raise

        class Reviewer(FakeReviewer):
            def __init__(self, task):
                super().__init__(gate)
                self.llm = adapter(Client(task.requirement.requirement_id), gate)

            async def run(self, task):
                if task.requirement.requirement_id == "review-req-0":
                    return verdict(task)
                try:
                    await self.llm.complete(messages=[], tools=[])
                except asyncio.CancelledError:
                    self.last_result = cancelled(
                        task, model_calls=self.llm.snapshot()["total_attempts"]
                    )
                    raise

        subject = EvidenceReviewRuntime(
            lambda task: Reviewer(task), FakeValidator(), resource_gate=gate
        )
        run = asyncio.create_task(subject.run(make_workers(6)))
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        results = subject.last_results
        assert len(results) == 6 and results[0].status == "passed"
        assert [result.requirement_id for result in results] == [
            f"review-req-{index}" for index in range(6)
        ]
        assert all(result.status == "not_reviewable" for result in results[1:])
        assert all(result.issue.code == "cancelled" for result in results[1:])
        assert len(client_started) == len(client_cancelled) == 2
        assert sum(result.trace.model_calls for result in results) == 2
        assert subject.trace.reviewer_model_calls == 2
        assert (
            subject.trace.passed_count == 1 and subject.trace.not_reviewable_count == 5
        )

    asyncio.run(scenario())


def test_physical_retry_attempts_are_reported_without_real_backoff_sleep():
    gate = ResourceGate()

    class Client:
        def __init__(self):
            self.calls = 0

        async def complete(self, *, messages, tools):
            self.calls += 1
            if self.calls <= 2:
                raise TimeoutError("Synthetic transient timeout")
            return LLMResponse()

    class Reviewer(FakeReviewer):
        def __init__(self):
            super().__init__(gate)
            self.llm = adapter(Client(), gate)

        async def run(self, task):
            await self.llm.complete(messages=[], tools=[], attempt_budget=3)
            stats = self.llm.snapshot()
            return verdict(
                task, model_calls=stats["total_attempts"], retry_count=stats["retries"]
            )

    subject = EvidenceReviewRuntime(
        lambda task: Reviewer(), FakeValidator(), resource_gate=gate
    )
    results = asyncio.run(subject.run(make_workers(2)))
    assert all(result.status == "passed" for result in results)
    assert [result.trace.model_calls for result in results] == [3, 3]
    assert subject.trace.reviewer_model_calls == 6 and subject.trace.retry_count == 4


def test_wrong_review_identity_is_typed_failure_without_changing_worker():
    gate = ResourceGate()

    class Reviewer(FakeReviewer):
        async def run(self, task):
            return verdict(task).model_copy(update={"requirement_id": "unknown-req"})

    worker = make_workers()[0]
    before = worker.model_dump()
    subject = EvidenceReviewRuntime(
        lambda task: Reviewer(gate), FakeValidator(), resource_gate=gate
    )
    result = asyncio.run(subject.run([worker]))[0]
    assert result.status == "not_reviewable" and result.issue is not None
    assert result.requirement_id == worker.requirements[0].requirement_id
    assert worker.model_dump() == before


def test_tampered_review_result_is_revalidated_before_accepting_verdict():
    gate = ResourceGate()

    class Reviewer(FakeReviewer):
        async def run(self, task):
            # model_copy 绕过 Pydantic 校验，模拟审核器返回不一致的类型实例。
            return verdict(task).model_copy(
                update={"reason_code": "evidence_does_not_support_claim"}
            )

    subject = EvidenceReviewRuntime(
        lambda task: Reviewer(gate), FakeValidator(), resource_gate=gate
    )
    result = asyncio.run(subject.run(make_workers()))[0]
    assert result.status == "not_reviewable" and result.issue is not None
    assert result.reason_code is None
    assert subject.trace.reviewed_count == 0


def test_cancelled_reviewer_without_snapshot_keeps_started_model_cost():
    async def scenario():
        entered = asyncio.Event()
        gate = ResourceGate()

        class Client:
            async def complete(self, *, messages, tools):
                entered.set()
                await asyncio.Event().wait()

        class Reviewer(FakeReviewer):
            def __init__(self):
                super().__init__(gate)
                self.llm = adapter(Client(), gate)

            async def run(self, task):
                # 不构建 last_result，验证池边界仍传播取消并核算已启动请求。
                await self.llm.complete(messages=[], tools=[])

        subject = EvidenceReviewRuntime(
            lambda task: Reviewer(), FakeValidator(), resource_gate=gate
        )
        run = asyncio.create_task(subject.run(make_workers()))
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        result = subject.last_results[0]
        assert result.status == "not_reviewable" and result.issue.code == "cancelled"
        assert result.trace.model_calls == subject.trace.reviewer_model_calls == 1

    asyncio.run(scenario())


def test_failed_reviewer_retains_physical_calls_and_retries():
    gate = ResourceGate()

    class Client:
        def __init__(self):
            self.calls = 0

        async def complete(self, *, messages, tools):
            self.calls += 1
            if self.calls <= 2:
                raise ConnectionError("Synthetic transient connection loss")
            return LLMResponse()

    class Reviewer(FakeReviewer):
        def __init__(self):
            super().__init__(gate)
            self.llm = adapter(Client(), gate)

        async def run(self, task):
            await self.llm.complete(messages=[], tools=[], attempt_budget=3)
            raise RuntimeError("Failure after actual model attempts")

    subject = EvidenceReviewRuntime(
        lambda task: Reviewer(), FakeValidator(), resource_gate=gate
    )
    result = asyncio.run(subject.run(make_workers()))[0]
    assert result.status == "not_reviewable" and result.issue is not None
    assert result.trace.model_calls == subject.trace.reviewer_model_calls == 3
    assert result.trace.retry_count == subject.trace.retry_count == 2


def test_blocking_provider_uses_existing_adapter_offload_and_resource_gate():
    async def scenario():
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        client_threads = []
        gate = ResourceGate(ResearchRuntimeConfig(max_concurrent_llm=1))

        class Client:
            def complete(self, *, messages, tools):
                client_threads.append(threading.get_ident())
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(timeout=2):
                    raise AssertionError("Fake thread barrier was not released")
                return LLMResponse()

        class Reviewer(FakeReviewer):
            def __init__(self):
                super().__init__(gate)
                self.llm = adapter(Client(), gate)

            async def run(self, task):
                await self.llm.complete(messages=[], tools=[])
                return verdict(task, model_calls=self.llm.snapshot()["total_attempts"])

        subject = EvidenceReviewRuntime(
            lambda task: Reviewer(), FakeValidator(), resource_gate=gate
        )
        run = asyncio.create_task(subject.run(make_workers()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert not run.done() and gate.stats["llm"]["active"] == 1
        finally:
            release.set()
        results = await run
        await subject.drain()
        assert results[0].status == "passed"
        assert client_threads and all(value != loop_thread for value in client_threads)
        assert subject.trace.reviewer_model_calls == 1
        assert subject.trace.peak_llm_concurrency == 1
        assert gate.stats["llm"]["active"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field", ["max_concurrent_reviewers", "max_model_calls_per_review"]
)
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2", None])
def test_review_limits_are_strict_positive_integers(field, value):
    with pytest.raises(ValidationError):
        ReviewRuntimeConfig(**{field: value})
