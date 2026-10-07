"""集中连接异步 Worker 与冻结的同步模型/工具接口。

取消等待 to_thread 的协程不能保证线程终止。阻塞操作由独立 Task 持有资源槽，
直到线程真正返回才释放；Provider 自身仍需配置请求超时。外层等待超时只限制
Worker 等待时间，不能强杀 Python 线程，也不代表后台操作已经停止。
"""

import asyncio
import inspect
import math
from collections.abc import Callable
from typing import Any

from app.llm.client import Message, ToolSchema
from app.llm.models import LLMResponse
from app.research.retry import (
    AdapterStats,
    BlockingOperationTimeout,
    DependencyCallError,
    RetryKind,
    RetryPolicy,
)
from app.research.runtime import ResourceGate, ResourceName
from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.redaction import public_value, redact_text


class _DependencyBoundary:
    """所有线程 offload 都位于这个边界，不散落到 Worker 业务代码。"""

    def __init__(self, gate: ResourceGate, timeout: float):
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Operation timeout must be a positive finite number")
        self.gate, self.timeout = gate, timeout
        self._pending: set[asyncio.Task] = set()

    def _finished(self, task: asyncio.Task):
        self._pending.discard(task)
        # 调用方可能已取消；读取异常防止后台完成时产生未消费异常警告。
        if not task.cancelled():
            task.exception()

    async def invoke(
        self,
        operation: Callable,
        resource: ResourceName,
        args: tuple,
        kwargs: dict,
        on_start: Callable[[], None],
    ):
        if inspect.iscoroutinefunction(operation):
            async with self.gate.slot(resource):
                on_start()
                # 原生异步操作超时/取消会直接传递到被 await 的依赖。
                return await asyncio.wait_for(
                    operation(*args, **kwargs), timeout=self.timeout
                )

        started = False

        async def blocking_operation():
            nonlocal started
            async with self.gate.slot(resource):
                started = True
                on_start()
                value = await asyncio.to_thread(operation, *args, **kwargs)
                # 兼容装饰器包装的 async 方法：若返回 awaitable，在资源槽内继续等待。
                return await value if inspect.isawaitable(value) else value

        task = asyncio.create_task(blocking_operation())
        self._pending.add(task)
        task.add_done_callback(self._finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=self.timeout)
        except asyncio.CancelledError:
            if not started:
                task.cancel()
            # 已启动的线程继续持有资源槽；取消立即传回 Worker，不等待整段阻塞操作。
            raise
        except TimeoutError:
            if task.done():
                # 依赖自身抛出的 TimeoutError 可由 RetryPolicy 安全地重试。
                raise
            if not started:
                task.cancel()
            # 后台线程未完成时不叠加重试，否则会把等待超时变成额外后台调用。
            raise BlockingOperationTimeout(
                "Blocking operation exceeded the adapter wait timeout."
            ) from None

    async def drain(self):
        """显式等待尚未结束的阻塞调用，供测试或退出清理使用，不用于取消路径。"""
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)


class _Adapter:
    def __init__(
        self,
        gate: ResourceGate,
        retry_policy: RetryPolicy | None,
        operation_timeout: float | None,
    ):
        self.resource_gate = gate
        config = gate.config
        self.retry_policy = retry_policy or RetryPolicy(
            config.max_retries,
            initial_delay=config.retry_initial_delay_seconds,
            jitter_seconds=config.retry_jitter_seconds,
        )
        self.stats = AdapterStats()
        self._boundary = _DependencyBoundary(
            gate,
            config.operation_timeout_seconds
            if operation_timeout is None
            else operation_timeout,
        )

    def snapshot(self) -> dict[str, int]:
        return self.stats.snapshot()

    async def drain(self):
        await self._boundary.drain()

    def _attempt_limit(self, budget: int | None) -> int:
        if budget is not None and (
            isinstance(budget, bool) or not isinstance(budget, int) or budget < 0
        ):
            raise ValueError("Attempt budget must be a nonnegative integer")
        maximum = self.retry_policy.max_retries + 1
        return maximum if budget is None else min(maximum, budget)

    async def _invoke(
        self,
        operation: Callable,
        resource: ResourceName,
        args: tuple,
        kwargs: dict,
        attempt: int,
    ):
        return await self._boundary.invoke(
            operation, resource, args, kwargs, lambda: self.stats.started(attempt)
        )


class AsyncLLMAdapter(_Adapter):
    """每个 Worker 独占一个适配器；所有 Worker 共享 ResourceGate。"""

    def __init__(
        self,
        client,
        gate: ResourceGate,
        *,
        retry_policy: RetryPolicy | None = None,
        operation_timeout: float | None = None,
    ):
        super().__init__(gate, retry_policy, operation_timeout)
        self.client = client

    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema],
        attempt_budget: int | None = None,
    ) -> LLMResponse:
        limit = self._attempt_limit(attempt_budget)
        self.stats.logical_dependency_calls += 1
        if not limit:
            raise DependencyCallError(code="budget_exhausted")
        before = self.stats.total_attempts
        for attempt in range(limit):
            try:
                result = await self._invoke(
                    self.client.complete,
                    "llm",
                    (),
                    {"messages": messages, "tools": tools},
                    attempt,
                )
                if not isinstance(result, LLMResponse):
                    raise TypeError("LLM adapter requires the standard LLMResponse")
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 公开适配边界按明确分类处理，不盲目重试
                kind = self.retry_policy.classify(exc)
                can_retry = kind == RetryKind.TRANSIENT and not isinstance(
                    exc, BlockingOperationTimeout
                )
                if can_retry and attempt + 1 < limit:
                    await self.retry_policy.wait(attempt)
                    continue
                details = self.retry_policy.details(exc)
                details.update(
                    classification=kind.value,
                    retry_exhausted=can_retry,
                    attempts=self.stats.total_attempts - before,
                    retry_count=max(0, self.stats.total_attempts - before - 1),
                )
                if isinstance(exc, BlockingOperationTimeout):
                    details["blocking_operation_may_continue"] = True
                raise DependencyCallError(
                    code=(
                        "budget_exhausted"
                        if can_retry and limit < self.retry_policy.max_retries + 1
                        else "dependency_failure"
                    ),
                    classification=kind,
                    attempts=self.stats.total_attempts - before,
                    failure=details,
                ) from None
        raise AssertionError("Attempt loop must return or raise")


class AsyncToolExecutorAdapter(_Adapter):
    def __init__(
        self,
        executor,
        gate: ResourceGate,
        *,
        retry_policy: RetryPolicy | None = None,
        operation_timeout: float | None = None,
    ):
        super().__init__(gate, retry_policy, operation_timeout)
        self.executor = executor

    @property
    def registry(self):
        return self.executor.registry

    @property
    def traces(self):
        return self.executor.traces

    @staticmethod
    def _error(name: str, details: dict) -> ToolObservation:
        return ToolObservation(
            status="error",
            tool_name=redact_text(name)
            if isinstance(name, str) and name.strip()
            else "invalid_tool_name",
            summary="Tool dependency failed after bounded retry handling.",
            payload={"failure": public_value(details)},
            issue=ObservationIssue(code="dependency_failure", retryable=True),
        )

    async def execute(
        self,
        tool_name: str,
        arguments: Any,
        *,
        tool_call_id: str | None = None,
        attempt_budget: int | None = None,
    ) -> ToolObservation:
        limit = self._attempt_limit(attempt_budget)
        self.stats.logical_dependency_calls += 1
        if not limit:
            return self._error(
                tool_name,
                {
                    "code": "budget_exhausted",
                    "message": "Tool attempt budget exhausted.",
                },
            )
        resource = "vlm" if tool_name == "analyze_figure" else "retrieval"
        before = self.stats.total_attempts
        for attempt in range(limit):
            pending_blocking = False
            try:
                result = await self._invoke(
                    self.executor.execute,
                    resource,
                    (tool_name, arguments),
                    {"tool_call_id": tool_call_id},
                    attempt,
                )
                if not isinstance(result, ToolObservation):
                    raise TypeError("Tool executor requires ToolObservation")
                # 原生异步执行器也经过公开脱敏边界，不输出路径、密钥或内部对象。
                result = ToolObservation.model_validate(
                    public_value(result.model_dump(mode="json"))
                )
                kind = self.retry_policy.classify(result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 转换为脱敏 Observation，取消已单独传播
                kind = self.retry_policy.classify(exc)
                details = self.retry_policy.details(exc)
                pending_blocking = isinstance(exc, BlockingOperationTimeout)
                if pending_blocking:
                    details["blocking_operation_may_continue"] = True
                result = self._error(tool_name, details)
            can_retry = kind == RetryKind.TRANSIENT and not pending_blocking
            if can_retry and attempt + 1 < limit:
                await self.retry_policy.wait(attempt)
                continue
            if result.status == "error":
                failure = dict(result.payload.get("failure", {}))
                failure.update(
                    classification=kind.value,
                    retry_exhausted=can_retry,
                    attempts=self.stats.total_attempts - before,
                    retry_count=max(0, self.stats.total_attempts - before - 1),
                )
                if can_retry and limit < self.retry_policy.max_retries + 1:
                    failure["code"] = "budget_exhausted"
                result = ToolObservation.model_validate(
                    result.model_dump()
                    | {"payload": result.payload | {"failure": failure}}
                )
            return result
        raise AssertionError("Attempt loop must return")
