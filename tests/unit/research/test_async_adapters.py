"""验证真实同步 ToolExecutor 的异步边界，以及重试计数和线程取消限制。"""

import asyncio
import threading

import pytest
from pydantic import BaseModel

from app.llm.models import LLMResponse
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.retry import DependencyCallError, RetryPolicy
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.registry import ToolRegistry


class HTTPFailure(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("private /home/local/path sk-private-token")


def success(name="search_text"):
    return ToolObservation(
        status="success",
        tool_name=name,
        summary="Observed prepared evidence.",
        payload={"evidence": [{"evidence_id": "ev-test"}]},
    )


def error(code="dependency_failure", **details):
    return ToolObservation(
        status="empty" if code == "empty_result" else "error",
        tool_name="search_text",
        summary="Public execution result.",
        payload={"failure": details},
        issue=ObservationIssue(code=code, retryable=code == "dependency_failure"),
    )


def fake_retry(delays, max_retries=2):
    async def sleeper(delay):
        delays.append(delay)

    return RetryPolicy(
        max_retries,
        initial_delay=0.2,
        jitter_seconds=0,
        sleeper=sleeper,
    )


class SequenceLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, *, messages, tools):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class SequenceExecutor:
    registry = ToolRegistry()
    traces = ()

    def __init__(self, observations):
        self.observations = iter(observations)
        self.calls = 0

    async def execute(self, name, arguments, *, tool_call_id=None):
        self.calls += 1
        response = next(self.observations)
        if isinstance(response, Exception):
            raise response
        return response


def test_native_async_llm_is_awaited_on_loop_and_does_not_block_other_tasks():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        loop_thread = threading.get_ident()

        class Client:
            async def complete(self, *, messages, tools):
                assert threading.get_ident() == loop_thread
                entered.set()
                await release.wait()
                return LLMResponse(content="public response")

        adapter = AsyncLLMAdapter(Client(), ResourceGate())
        run = asyncio.create_task(adapter.complete(messages=[], tools=[]))
        await asyncio.wait_for(entered.wait(), 1)
        assert not run.done()
        release.set()
        result = await run
        assert result.content == "public response"
        assert adapter.snapshot()["total_attempts"] == 1

    asyncio.run(scenario())


def test_existing_sync_tool_executor_offloads_without_blocking_event_loop():
    async def scenario():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        entered = asyncio.Event()
        release = threading.Event()
        threads = []

        class Input(BaseModel):
            query: str

        class LegacyTool(Tool):
            name = "search_text"
            description = "Deterministic prepared evidence."
            input_model = Input

            def execute(self, arguments):
                threads.append(threading.get_ident())
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2), "Event loop failed to release blocking tool"
                return success()

        registry = ToolRegistry()
        registry.register(LegacyTool())
        executor = ToolExecutor(registry)
        adapter = AsyncToolExecutorAdapter(executor, ResourceGate())
        run = asyncio.create_task(
            adapter.execute("search_text", {"query": "test"}, tool_call_id="tool-1")
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert threads == [threads[0]] and threads[0] != loop_thread
            assert not run.done()
        finally:
            release.set()
        result = await run
        assert result.status == "success"
        assert adapter.registry is registry and adapter.traces == executor.traces
        assert adapter.traces[0].tool_call_id == "tool-1"
        assert adapter.snapshot()["total_attempts"] == 1
        await adapter.drain()

    asyncio.run(scenario())


def test_transient_llm_retries_with_injected_backoff_and_physical_counts():
    delays = []
    client = SequenceLLM([HTTPFailure(429), HTTPFailure(503), LLMResponse()])
    adapter = AsyncLLMAdapter(client, ResourceGate(), retry_policy=fake_retry(delays))
    result = asyncio.run(adapter.complete(messages=[], tools=[]))
    assert isinstance(result, LLMResponse) and client.calls == 3
    assert delays == [0.2, 0.4]
    assert adapter.snapshot() == {
        "total_attempts": 3,
        "retries": 2,
        "logical_dependency_calls": 1,
        "retried_dependency_calls": 1,
    }


@pytest.mark.parametrize("status", [400, 401])
def test_permanent_llm_failure_is_not_retried_and_public_failure_is_redacted(status):
    delays = []
    client = SequenceLLM([HTTPFailure(status)])
    adapter = AsyncLLMAdapter(client, ResourceGate(), retry_policy=fake_retry(delays))
    with pytest.raises(DependencyCallError) as failure:
        asyncio.run(adapter.complete(messages=[], tools=[]))
    assert client.calls == 1 and delays == []
    assert failure.value.failure["http_status"] == status
    serialized = str(failure.value.failure)
    assert "sk-private-token" not in serialized and "/home/local" not in serialized


def test_transient_retry_exhaustion_has_bounded_attempts():
    delays = []
    client = SequenceLLM([TimeoutError(), TimeoutError(), TimeoutError()])
    adapter = AsyncLLMAdapter(client, ResourceGate(), retry_policy=fake_retry(delays))
    with pytest.raises(DependencyCallError) as failure:
        asyncio.run(adapter.complete(messages=[], tools=[]))
    assert failure.value.code == "dependency_failure"
    assert failure.value.attempts == client.calls == 3
    assert failure.value.failure["retry_exhausted"] is True
    assert delays == [0.2, 0.4]


def test_worker_attempt_budget_counts_retries_and_does_not_start_extra_call():
    delays = []
    client = SequenceLLM([ConnectionError(), ConnectionError(), LLMResponse()])
    adapter = AsyncLLMAdapter(client, ResourceGate(), retry_policy=fake_retry(delays))
    with pytest.raises(DependencyCallError) as failure:
        asyncio.run(adapter.complete(messages=[], tools=[], attempt_budget=2))
    assert failure.value.code == "budget_exhausted"
    assert failure.value.attempts == client.calls == 2
    assert adapter.snapshot()["total_attempts"] == 2
    assert delays == [0.2]


def test_zero_attempt_budget_never_calls_dependency():
    adapter = AsyncLLMAdapter(SequenceLLM([]), ResourceGate())
    with pytest.raises(DependencyCallError) as failure:
        asyncio.run(adapter.complete(messages=[], tools=[], attempt_budget=0))
    assert failure.value.code == "budget_exhausted"
    assert adapter.snapshot()["total_attempts"] == 0


@pytest.mark.parametrize("code", ["invalid_arguments", "unknown_paper", "empty_result"])
def test_tool_recovery_and_empty_observations_are_not_automatically_retried(code):
    delays = []
    executor = SequenceExecutor([error(code)])
    adapter = AsyncToolExecutorAdapter(
        executor, ResourceGate(), retry_policy=fake_retry(delays)
    )
    result = asyncio.run(adapter.execute("search_text", {}))
    assert result.issue.code == code and executor.calls == 1
    assert delays == [] and adapter.snapshot()["retries"] == 0


def test_tool_dependency_observations_retry_only_explicit_transient_diagnostics():
    delays = []
    executor = SequenceExecutor(
        [error(http_status=429), error(http_status=502), success()]
    )
    adapter = AsyncToolExecutorAdapter(
        executor, ResourceGate(), retry_policy=fake_retry(delays)
    )
    result = asyncio.run(adapter.execute("search_text", {}))
    assert result.status == "success" and executor.calls == 3
    assert delays == [0.2, 0.4]
    assert adapter.snapshot()["retried_dependency_calls"] == 1


def test_tool_exhaustion_and_permanent_failure_are_observations():
    delays = []
    exhausted = AsyncToolExecutorAdapter(
        SequenceExecutor([error(http_status=503)] * 3),
        ResourceGate(),
        retry_policy=fake_retry(delays),
    )
    result = asyncio.run(exhausted.execute("search_text", {}))
    assert result.status == "error" and result.issue.code == "dependency_failure"
    assert result.payload["failure"]["attempts"] == 3
    assert result.payload["failure"]["retry_exhausted"] is True
    permanent = AsyncToolExecutorAdapter(
        SequenceExecutor([error(http_status=401)]),
        ResourceGate(),
        retry_policy=fake_retry(delays),
    )
    result = asyncio.run(permanent.execute("search_text", {}))
    assert result.payload["failure"]["attempts"] == 1
    assert permanent.snapshot()["retries"] == 0


def test_tool_adapter_uses_separate_vlm_and_retrieval_gates():
    async def scenario():
        gate = ResourceGate()
        entered = asyncio.Event()
        release = asyncio.Event()
        started = []

        class Executor(SequenceExecutor):
            async def execute(self, name, arguments, *, tool_call_id=None):
                started.append(name)
                if len(started) == 2:
                    entered.set()
                await release.wait()
                return success(name)

        adapter = AsyncToolExecutorAdapter(Executor([]), gate)
        operations = [
            asyncio.create_task(adapter.execute(name, {}))
            for name in ["analyze_figure", "search_text", "analyze_figure", "read_page"]
        ]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert set(started) == {"analyze_figure", "search_text"}
            assert gate.stats["vlm"]["active"] == 1
            assert gate.stats["retrieval"]["active"] == 1
        finally:
            release.set()
            await asyncio.gather(*operations)
        assert gate.peaks == {"llm": 0, "vlm": 1, "retrieval": 1}

    asyncio.run(scenario())


def test_cancelled_blocking_wait_keeps_resource_slot_until_thread_returns():
    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        gate = ResourceGate(ResearchRuntimeConfig(max_concurrent_llm=1))
        calls = []

        class Client:
            def complete(self, *, messages, tools):
                calls.append(len(calls) + 1)
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2)
                return LLMResponse()

        adapter = AsyncLLMAdapter(Client(), gate)
        first = asyncio.create_task(adapter.complete(messages=[], tools=[]))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert gate.stats["llm"]["active"] == 1
            second = asyncio.create_task(adapter.complete(messages=[], tools=[]))
            # 仅让调度器处理第二个请求；没有真实计时延迟。
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert calls == [1] and not second.done()
        finally:
            release.set()
        await adapter.drain()
        await second
        assert calls == [1, 2] and gate.peaks["llm"] == 1
        assert gate.stats["llm"]["active"] == 0

    asyncio.run(scenario())


def test_cancellation_during_backoff_propagates_without_another_attempt():
    async def scenario():
        backoff_started = asyncio.Event()

        async def sleeper(delay):
            backoff_started.set()
            await asyncio.Event().wait()

        client = SequenceLLM([HTTPFailure(429), LLMResponse()])
        gate = ResourceGate()
        adapter = AsyncLLMAdapter(
            client, gate, retry_policy=RetryPolicy(sleeper=sleeper)
        )
        run = asyncio.create_task(adapter.complete(messages=[], tools=[]))
        await asyncio.wait_for(backoff_started.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert client.calls == adapter.snapshot()["total_attempts"] == 1
        assert gate.stats["llm"]["active"] == 0

    asyncio.run(scenario())


def test_blocking_wait_timeout_does_not_retry_or_release_running_thread(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        gate = ResourceGate(ResearchRuntimeConfig(max_concurrent_llm=1))
        calls = []

        class Client:
            def complete(self, *, messages, tools):
                calls.append(1)
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2)
                return LLMResponse()

        async def synthetic_deadline(awaitable, *, timeout):
            # 模拟等待超时；线程已经进入，无须依赖真实计时或 sleep。
            await entered.wait()
            raise TimeoutError

        monkeypatch.setattr(
            "app.research.async_tools.asyncio.wait_for", synthetic_deadline
        )
        adapter = AsyncLLMAdapter(Client(), gate)
        try:
            with pytest.raises(DependencyCallError) as failure:
                await adapter.complete(messages=[], tools=[])
            assert failure.value.failure["blocking_operation_may_continue"] is True
            assert adapter.snapshot()["total_attempts"] == len(calls) == 1
            assert adapter.snapshot()["retries"] == 0
            assert gate.stats["llm"]["active"] == 1
        finally:
            release.set()
            await adapter.drain()
        assert gate.stats["llm"]["active"] == 0

    asyncio.run(scenario())


def test_cancelling_queued_dependency_does_not_start_physical_call():
    async def scenario():
        gate = ResourceGate(ResearchRuntimeConfig(max_concurrent_llm=1))
        client = SequenceLLM([LLMResponse()])
        adapter = AsyncLLMAdapter(client, gate)
        async with gate.slot("llm"):
            task = asyncio.create_task(adapter.complete(messages=[], tools=[]))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert adapter.snapshot()["total_attempts"] == client.calls == 0
        assert gate.stats["llm"]["active"] == 0

    asyncio.run(scenario())
