"""只对明确的短暂依赖故障重试，业务结果和参数错误保留给 Worker。"""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any

from app.diagnostics import failure_details
from app.tools.observations import ToolObservation


class RetryKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    AGENT_RECOVERABLE = "agent_recoverable"
    BUSINESS_RESULT = "business_result"


@dataclass
class AdapterStats:
    """attempts 包含每次真实重试；排队但尚未开始的操作不算物理调用。"""

    total_attempts: int = 0
    retries: int = 0
    logical_dependency_calls: int = 0
    retried_dependency_calls: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)

    def started(self, attempt: int):
        self.total_attempts += 1
        if attempt:
            self.retries += 1
        if attempt == 1:
            self.retried_dependency_calls += 1


class DependencyCallError(Exception):
    """适配边界抛出的安全错误；Worker 不需要理解 HTTP 状态码。"""

    def __init__(
        self,
        *,
        code: str = "dependency_failure",
        classification: RetryKind = RetryKind.PERMANENT,
        attempts: int = 0,
        failure: dict[str, Any] | None = None,
    ):
        self.code = code
        self.classification = classification
        self.attempts = attempts
        self.failure = failure or {
            "phase": "dependency",
            "message": "Dependency call could not complete.",
        }
        super().__init__(
            "Dependency attempt budget exhausted."
            if code == "budget_exhausted"
            else "Dependency call failed after bounded retry handling."
        )


class BlockingOperationTimeout(TimeoutError):
    """等待超时后线程可能仍在运行，不能立即发起另一个相同操作。"""


class RetryPolicy:
    def __init__(
        self,
        max_retries: int = 2,
        *,
        initial_delay: float = 0.1,
        jitter_seconds: float = 0.05,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = perf_counter,
        jitter: Callable[[], float] = random.random,
    ):
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
            or not math.isfinite(initial_delay)
            or not math.isfinite(jitter_seconds)
            or initial_delay < 0
            or jitter_seconds < 0
        ):
            raise ValueError("Retry limits and delays must be nonnegative")
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.jitter_seconds = jitter_seconds
        self.sleeper, self.clock, self.jitter = sleeper, clock, jitter

    @staticmethod
    def _diagnostic_kind(details: dict[str, Any]) -> RetryKind:
        status = details.get("http_status")
        if isinstance(status, int) and status in {429, 502, 503}:
            return RetryKind.TRANSIENT
        if isinstance(status, int):
            return RetryKind.PERMANENT
        types = [details.get("error_type", ""), details.get("cause_type", "")]
        if any(
            "Timeout" in t or "Connection" in t for t in types if isinstance(t, str)
        ):
            return RetryKind.TRANSIENT
        return RetryKind.PERMANENT

    def classify(self, outcome: Exception | ToolObservation) -> RetryKind:
        if isinstance(outcome, ToolObservation):
            if outcome.status in {"success", "empty"}:
                return RetryKind.BUSINESS_RESULT
            code = outcome.issue.code
            if code in {
                "invalid_arguments",
                "unknown_paper",
                "unknown_tool",
                "invalid_evidence",
                "citation_validation_failure",
                "invalid_expansion",
            }:
                return RetryKind.AGENT_RECOVERABLE
            if code == "dependency_failure":
                details = outcome.payload.get("failure", {})
                return (
                    self._diagnostic_kind(details)
                    if isinstance(details, dict)
                    else RetryKind.PERMANENT
                )
            return RetryKind.PERMANENT
        cause = outcome
        for _ in range(8):
            status = getattr(cause, "status_code", None)
            if isinstance(status, int):
                return self._diagnostic_kind({"http_status": status})
            if isinstance(cause, (TimeoutError, ConnectionError)):
                return RetryKind.TRANSIENT
            kind = self._diagnostic_kind({"error_type": type(cause).__name__})
            if kind == RetryKind.TRANSIENT:
                return kind
            nested = cause.__cause__
            if not isinstance(nested, Exception) or nested is cause:
                break
            cause = nested
        return RetryKind.PERMANENT

    def delay(self, retry_index: int) -> float:
        if (
            isinstance(retry_index, bool)
            or not isinstance(retry_index, int)
            or retry_index < 0
        ):
            raise ValueError("Retry index must be a nonnegative integer")
        return self.initial_delay * 2**retry_index + self.jitter_seconds * max(
            0.0, min(1.0, self.jitter())
        )

    async def wait(self, retry_index: int):
        await self.sleeper(self.delay(retry_index))

    def details(self, exc: Exception) -> dict:
        return failure_details(
            exc, phase="async_dependency", summary="Dependency operation failed."
        )
