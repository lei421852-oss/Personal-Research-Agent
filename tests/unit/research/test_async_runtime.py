"""资源限制与重试分类的确定性测试；延迟通过伪 sleeper 收集，不真实等待。"""

import asyncio

import pytest
from pydantic import ValidationError

from app.research.retry import RetryKind, RetryPolicy
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.tools.observations import ObservationIssue, ToolObservation


class HTTPFailure(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("private-provider-detail")


def observation(status="error", code="dependency_failure", **failure):
    return ToolObservation(
        status=status,
        tool_name="search_text",
        summary="Public execution result.",
        payload={"failure": failure},
        issue=ObservationIssue(code=code, retryable=code == "dependency_failure"),
    )


@pytest.mark.parametrize("status", [429, 502, 503])
def test_retry_classifies_transient_http_errors(status):
    policy = RetryPolicy()
    assert policy.classify(HTTPFailure(status)) == RetryKind.TRANSIENT
    assert policy.classify(observation(http_status=status)) == RetryKind.TRANSIENT


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_retry_does_not_assume_all_provider_errors_are_transient(status):
    policy = RetryPolicy()
    assert policy.classify(HTTPFailure(status)) == RetryKind.PERMANENT
    assert policy.classify(observation(http_status=status)) == RetryKind.PERMANENT


@pytest.mark.parametrize(
    "failure", [TimeoutError(), ConnectionResetError(), ConnectionError()]
)
def test_retry_classifies_connection_and_timeout_failures(failure):
    assert RetryPolicy().classify(failure) == RetryKind.TRANSIENT
    wrapped = RuntimeError("Wrapped dependency failure")
    wrapped.__cause__ = failure
    assert RetryPolicy().classify(wrapped) == RetryKind.TRANSIENT


@pytest.mark.parametrize(
    "code", ["invalid_arguments", "unknown_paper", "invalid_evidence"]
)
def test_argument_failures_are_for_agent_recovery_not_provider_retry(code):
    assert RetryPolicy().classify(observation(code=code)) == RetryKind.AGENT_RECOVERABLE


def test_empty_result_is_business_result_and_unknown_dependency_is_not_retried():
    policy = RetryPolicy()
    assert (
        policy.classify(observation("empty", "empty_result"))
        == RetryKind.BUSINESS_RESULT
    )
    assert policy.classify(observation()) == RetryKind.PERMANENT
    assert (
        policy.classify(ValueError("Malformed business input")) == RetryKind.PERMANENT
    )


def test_retry_backoff_and_jitter_without_real_sleep():
    delays = []

    async def sleeper(delay):
        delays.append(delay)

    policy = RetryPolicy(
        initial_delay=0.2,
        jitter_seconds=0.1,
        jitter=lambda: 0.5,
        sleeper=sleeper,
    )
    asyncio.run(policy.wait(0))
    asyncio.run(policy.wait(1))
    asyncio.run(policy.wait(2))
    assert delays == pytest.approx([0.25, 0.45, 0.85])
    with pytest.raises(ValueError):
        policy.delay(-1)


@pytest.mark.parametrize(
    "field",
    [
        "max_concurrent_workers",
        "max_concurrent_llm",
        "max_concurrent_vlm",
        "max_concurrent_retrieval",
        "max_tool_calls_per_worker",
        "max_model_calls_per_worker",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_runtime_limits_validate_before_execution(field, value):
    with pytest.raises(ValidationError):
        ResearchRuntimeConfig(**{field: value})


def test_resource_caps_are_independent_and_allow_different_resources_to_overlap():
    async def scenario():
        config = ResearchRuntimeConfig(
            max_concurrent_workers=3,
            max_concurrent_llm=2,
            max_concurrent_vlm=1,
            max_concurrent_retrieval=1,
        )
        gate = ResourceGate(config)
        release = asyncio.Event()
        full = asyncio.Event()
        starts = {name: 0 for name in ["llm", "vlm", "retrieval"]}

        async def operation(resource):
            async with gate.slot(resource):
                starts[resource] += 1
                if starts == {"llm": 2, "vlm": 1, "retrieval": 1}:
                    full.set()
                await release.wait()

        operations = [
            asyncio.create_task(operation(resource))
            for resource in [
                "llm",
                "llm",
                "vlm",
                "retrieval",
                "llm",
                "vlm",
                "retrieval",
            ]
        ]
        try:
            await asyncio.wait_for(full.wait(), 1)
            assert {name: stats["active"] for name, stats in gate.stats.items()} == {
                "llm": 2,
                "vlm": 1,
                "retrieval": 1,
            }
            # 所有资源共享一个全局槽的话，这四个操作无法同时进入。
            assert sum(s["active"] for s in gate.stats.values()) == 4
        finally:
            release.set()
            await asyncio.gather(*operations)
        assert gate.peaks == {"llm": 2, "vlm": 1, "retrieval": 1}
        assert all(stats["active"] == 0 for stats in gate.stats.values())

    asyncio.run(scenario())


def test_cancelled_native_resource_slot_is_released():
    async def scenario():
        gate = ResourceGate()
        entered = asyncio.Event()

        async def operation():
            async with gate.slot("vlm"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(operation())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate.stats["vlm"]["active"] == 0
        async with gate.slot("vlm"):
            assert gate.stats["vlm"]["active"] == 1
        assert gate.peaks["vlm"] == 1

    asyncio.run(scenario())
