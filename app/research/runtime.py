"""集中管理研究 Worker 的硬预算和各类依赖的并发配额。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

PositiveInt = Annotated[StrictInt, Field(gt=0)]
ResourceName = Literal["llm", "vlm", "retrieval"]


class ResearchRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_concurrent_workers: PositiveInt = 3
    max_concurrent_llm: PositiveInt = 2
    max_concurrent_vlm: PositiveInt = 1
    # 当前检索使用 CPU 模型；先串行使用共享模型，避免线程同时调用造成争抢。
    max_concurrent_retrieval: PositiveInt = 1
    max_tool_calls_per_worker: PositiveInt = 8
    max_model_calls_per_worker: PositiveInt = 20
    # 策略协议纠正与依赖重试独立计数；0 表示不提供纠正机会。
    max_protocol_corrections: Annotated[StrictInt, Field(ge=0)] = 2
    recent_observations: PositiveInt = 4
    max_context_evidence: PositiveInt = 20
    operation_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_retries: Annotated[StrictInt, Field(ge=0)] = 2
    retry_initial_delay_seconds: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    retry_jitter_seconds: float = Field(default=0.05, ge=0, allow_inf_nan=False)


class ResourceGate:
    """资源槽和 Worker 槽彼此独立；同一运行必须共享这个 Gate。"""

    def __init__(self, config: ResearchRuntimeConfig | None = None):
        self.config = config or ResearchRuntimeConfig()
        self.limits = {
            "llm": self.config.max_concurrent_llm,
            "vlm": self.config.max_concurrent_vlm,
            "retrieval": self.config.max_concurrent_retrieval,
        }
        self.llm_semaphore = asyncio.Semaphore(self.limits["llm"])
        self.vlm_semaphore = asyncio.Semaphore(self.limits["vlm"])
        self.retrieval_semaphore = asyncio.Semaphore(self.limits["retrieval"])
        self._semaphores = {
            "llm": self.llm_semaphore,
            "vlm": self.vlm_semaphore,
            "retrieval": self.retrieval_semaphore,
        }
        self._active = dict.fromkeys(self.limits, 0)
        self._peaks = dict.fromkeys(self.limits, 0)

    @property
    def peaks(self) -> dict[str, int]:
        return self._peaks.copy()

    @property
    def stats(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "active": self._active[name],
                "peak": self._peaks[name],
                "limit": limit,
            }
            for name, limit in self.limits.items()
        }

    @asynccontextmanager
    async def slot(self, resource: ResourceName) -> AsyncIterator[None]:
        if resource not in self._semaphores:
            raise ValueError("Unknown resource type")
        async with self._semaphores[resource]:
            self._active[resource] += 1
            self._peaks[resource] = max(self._peaks[resource], self._active[resource])
            try:
                yield
            finally:
                self._active[resource] -= 1
