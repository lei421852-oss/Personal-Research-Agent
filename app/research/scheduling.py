"""admission 临界区：覆盖「数据库已提交、Task 尚未注册」的可观测窗口。

契约（阶段 2.4 / R4）：

1. 创建记录前先登记 ``Operation``，``create_run`` 提交成功后进入 ``registered``，
   之后才 ``create_task``。这样「已提交但尚未注册」不再是不可见状态。
2. 重放方在同键记录上等待 ``Operation`` 落定，等待**有界且不持数据库事务**；
   超时即判定「调度结果未知」，绝不猜测，也绝不建第二个 Task。
3. 数据库唯一约束仍是最终裁决；本模块只提供可见性与等待，不参与裁决，
   因此失败方无需在此模块内 rollback —— 裁决与回滚留在 Repository。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum

DEFAULT_ADMISSION_TIMEOUT = 5.0
DEFAULT_FAILED_RETENTION = 32


class AdmissionState(StrEnum):
    """一次 admission 的三种可见状态。"""

    SUBMITTING = "submitting"  # 提交进行中，或已提交但 Task 尚未注册
    REGISTERED = "registered"  # Task 已注册，本实例持有执行
    FAILED = "failed"  # 已确认未安排（提交回滚，或调度失败且收尾完成）


@dataclass(frozen=True)
class SchedulingFailure:
    """已确认的 admission 失败；等待方原样复制为公开错误，不自行改写。"""

    code: str
    message: str
    status_code: int
    retry_mode: str


@dataclass
class Operation:
    """单个 run 的 admission 登记。``done`` 只在落定时置位，不会被回退。"""

    run_id: str
    state: AdmissionState = AdmissionState.SUBMITTING
    task: asyncio.Task | None = None
    failure: SchedulingFailure | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def settled(self) -> bool:
        return self.state is not AdmissionState.SUBMITTING

    @property
    def registered(self) -> bool:
        return self.state is AdmissionState.REGISTERED

    @property
    def failed(self) -> bool:
        return self.state is AdmissionState.FAILED


class AdmissionRegistry:
    """每个 Manager 实例一份；只登记本实例发起的 admission。"""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_ADMISSION_TIMEOUT,
        failed_retention: int = DEFAULT_FAILED_RETENTION,
    ) -> None:
        if timeout <= 0:
            raise ValueError("admission timeout must be positive")
        if failed_retention < 0:
            raise ValueError("failed retention must be non-negative")
        self.timeout = timeout
        self.failed_retention = failed_retention
        self._operations: dict[str, Operation] = {}

    def __len__(self) -> int:
        return len(self._operations)

    def begin(self, run_id: str) -> Operation:
        """创建前登记；重复登记属于调用方缺陷，直接暴露。"""

        if run_id in self._operations:
            raise ValueError("admission already registered")
        operation = Operation(run_id=run_id)
        self._operations[run_id] = operation
        return operation

    def peek(self, run_id: str) -> Operation | None:
        return self._operations.get(run_id)

    def mark_registered(
        self, run_id: str, task: asyncio.Task | None
    ) -> Operation | None:
        """Task 已注册；此后 ``_is_attached`` 可据真实 Task 判定，不靠本登记。"""

        operation = self._operations.get(run_id)
        if operation is None or operation.settled:
            return operation
        operation.task = task
        operation.state = AdmissionState.REGISTERED
        operation.done.set()
        return operation

    def mark_failed(self, run_id: str, failure: SchedulingFailure) -> Operation | None:
        """已确认未安排；保留失败原因供同键重放方复制为同一公开错误。"""

        operation = self._operations.get(run_id)
        if operation is None or operation.settled:
            return operation
        operation.failure = failure
        operation.state = AdmissionState.FAILED
        operation.done.set()
        return operation

    def discard(self, run_id: str) -> Operation | None:
        return self._operations.pop(run_id, None)

    def retain(self, keep: set[str]) -> None:
        """淘汰已离开内存的 run 的登记；已失败登记单独按条数上限保留。"""

        for run_id in list(self._operations):
            if run_id in keep:
                continue
            if self._operations[run_id].failed:
                continue
            del self._operations[run_id]
        stale = [
            run_id
            for run_id, operation in self._operations.items()
            if operation.failed and run_id not in keep
        ]
        for run_id in stale[: max(0, len(stale) - self.failed_retention)]:
            del self._operations[run_id]

    def clear(self) -> None:
        self._operations.clear()

    async def settled(
        self, run_id: str, *, timeout: float | None = None
    ) -> Operation | None:
        """等待落定。

        返回 ``None`` 表示仍在 ``submitting``（等待超时），调用方必须按
        「调度结果未知」处理，不得据此认定未调度成功。
        """

        operation = self._operations.get(run_id)
        if operation is None or operation.settled:
            return operation
        limit = self.timeout if timeout is None else timeout
        try:
            await asyncio.wait_for(operation.done.wait(), limit)
        except TimeoutError:
            return None
        return operation
