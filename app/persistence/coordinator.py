"""终态协调器：每个 run 唯一收尾任务、提交阶段判定、只读核对与故障 latch。

判定契约（阶段 2.3 统一版）：
- 提交成功 → committed
- 提交前故障且回滚成功 → rolled_back
- COMMIT 已开始但结果不可知，或无法证明回滚 → unknown
- unknown 只允许 reconcile_finalize 只读核对，绝不重新提交
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.persistence.failure_diagnostics import (
    build_failure,
    emit_failure,
    record_emit_error,
)
from app.persistence.repository import (
    EventCommand,
    FinalizeOutcome,
    ReconcileOutcome,
    RepositoryError,
)
from app.persistence.serialization import FinalizeCommand

DEFAULT_SHUTDOWN_TIMEOUT = 10.0


class PersistenceLatched(RepositoryError):
    def __init__(self, reason: str):
        super().__init__("persistence_latched", detail=reason)


class PersistenceLatch:
    """每个 run 独立的持久化故障标记；一旦置位，禁止新写入与新调度。

    保留**首次**触发原因用于追溯；最新判定细节记录在 TerminalRecord.reason。
    """

    def __init__(self) -> None:
        self._reason: str | None = None
        self._diagnostic: dict | None = None
        self._unresolved_writer_failure = False
        self._event = asyncio.Event()

    @property
    def tripped(self) -> bool:
        return self._reason is not None

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def diagnostic(self) -> dict | None:
        return dict(self._diagnostic) if self._diagnostic is not None else None

    def trip(self, reason: str, *, diagnostic: dict | None = None) -> bool:
        # 调用方在所属事件循环串行调用；不声称跨进程同步。
        if self._reason is not None:
            return False
        self._reason = reason
        self._diagnostic = dict(diagnostic) if diagnostic is not None else None
        self._event.set()
        return True

    def record_diagnostic(self, diagnostic: dict) -> None:
        """仅在锁存后补充安全信息，不覆盖首次诊断。"""
        if self._reason is not None and self._diagnostic is None:
            self._diagnostic = dict(diagnostic)

    def note_unresolved_writer_failure(self) -> None:
        """保留锁存期间在途 Writer 的额外故障，阻止自动核对解锁。"""
        if self._reason is not None:
            self._unresolved_writer_failure = True

    def clear(self, reason: str | None = None) -> None:
        """只允许解除由只读核对确认后才可解除的标记。"""

        if reason is not None and (
            self._reason != reason or self._unresolved_writer_failure
        ):
            return
        self._reason = None
        self._diagnostic = None
        self._unresolved_writer_failure = False
        self._event.clear()

    async def wait(self) -> str:
        await self._event.wait()
        return self._reason or ""

    def require_clear(self) -> None:
        if self.tripped:
            raise PersistenceLatched(self._reason or "persistence_latched")


class TerminalPhase(StrEnum):
    IDLE = "idle"
    IN_FLIGHT = "in_flight"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass
class TerminalRecord:
    run_id: str
    phase: TerminalPhase = TerminalPhase.IDLE
    token: str | None = None
    sha256: str | None = None
    last_outcome: str | None = None
    reason: str | None = None

    @property
    def can_submit(self) -> bool:
        return self.phase in (TerminalPhase.IDLE, TerminalPhase.ROLLED_BACK)


class TerminalCoordinator:
    """串行化同一 run 的终态收尾，并在未知结果时只做只读核对。"""

    def __init__(
        self,
        repository,
        *,
        writer=None,
        latch: PersistenceLatch | None = None,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        self.repository = repository
        self.writer = writer
        self.latch = latch or PersistenceLatch()
        self.shutdown_timeout = shutdown_timeout
        self._records: dict[str, TerminalRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False

    def record(self, run_id: str) -> TerminalRecord:
        return self._records.setdefault(run_id, TerminalRecord(run_id=run_id))

    def _trip_with_diagnostic(self, reason: str, *, run_id: str) -> None:
        """只为赢得首次锁存的调用记录一次安全诊断。"""
        if not self.latch.trip(reason):
            return
        try:
            stage = (
                "coordinator_reconcile"
                if reason
                in {
                    "finalize_content_conflict",
                    "finalize_pending_review",
                    "finalize_reconcile_unavailable",
                }
                else "coordinator_finalize"
            )
            event = build_failure(
                run_id=run_id,
                operation="reconcile"
                if stage == "coordinator_reconcile"
                else "finalize",
                stage=stage,
                event_type=None,
                exc=asyncio.CancelledError()
                if reason == "finalize_cancelled"
                else None,
                reason=reason,
            )
            self.latch.record_diagnostic(event)
            emit_failure(event)
        except Exception:  # noqa: BLE001 - 诊断错误不能影响原始锁存
            record_emit_error()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending_run_ids(self) -> tuple[str, ...]:
        return tuple(run_id for run_id, task in self._tasks.items() if not task.done())

    async def finalize(
        self,
        command: FinalizeCommand,
        *,
        trailing_events: Sequence[EventCommand] = (),
    ) -> FinalizeOutcome:
        """首次提交或已证明回滚后的重试；同一 run 只有一个收尾任务。"""

        if self._closed:
            raise RepositoryError("coordinator_closed")
        self.latch.require_clear()

        existing = self._tasks.get(command.run_id)
        if existing is not None and not existing.done():
            return await asyncio.shield(existing)

        record = self.record(command.run_id)
        if record.token not in (None, command.finalize_token):
            raise RepositoryError("finalize_token_mismatch", run_id=command.run_id)
        if record.phase is TerminalPhase.UNKNOWN:
            # 未知结果不允许写路径继续；调用方必须走 reconcile。
            raise RepositoryError("finalize_requires_reconcile", run_id=command.run_id)
        if record.phase is TerminalPhase.COMMITTED:
            return FinalizeOutcome.ALREADY_COMMITTED
        if record.phase is TerminalPhase.IN_FLIGHT:
            raise RepositoryError("finalize_in_flight", run_id=command.run_id)

        task = asyncio.create_task(
            self._run_finalize(command, trailing_events),
            name=f"finalize:{command.run_id}",
        )
        self._tasks[command.run_id] = task
        return await asyncio.shield(task)

    async def _run_finalize(
        self,
        command: FinalizeCommand,
        trailing_events: Sequence[EventCommand],
    ) -> FinalizeOutcome:
        record = self.record(command.run_id)
        record.token = command.finalize_token
        record.sha256 = command.finalize_sha256
        record.phase = TerminalPhase.IN_FLIGHT
        try:
            if self.writer is not None:
                # 先让已接受的事件与报告落库；屏障失败等于收尾前故障。
                await self.writer.flush()
            outcome = await self.repository.finalize_run(
                command, trailing_events=trailing_events
            )
        except asyncio.CancelledError:
            record.phase = TerminalPhase.UNKNOWN
            record.reason = "finalize_cancelled"
            self._trip_with_diagnostic("finalize_cancelled", run_id=command.run_id)
            raise
        except Exception as exc:  # 统一收口为协调器状态
            record.phase = TerminalPhase.FAILED
            record.reason = getattr(exc, "code", type(exc).__name__)
            raise

        return self._apply(record, outcome.outcome)

    def _apply(
        self, record: TerminalRecord, outcome: FinalizeOutcome
    ) -> FinalizeOutcome:
        record.last_outcome = outcome.value
        if outcome in (
            FinalizeOutcome.NEW,
            FinalizeOutcome.ALREADY_COMMITTED,
            FinalizeOutcome.FOREIGN_TERMINAL,
        ):
            record.phase = TerminalPhase.COMMITTED
            record.reason = (
                "foreign_terminal"
                if outcome is FinalizeOutcome.FOREIGN_TERMINAL
                else None
            )
            return outcome
        if outcome is FinalizeOutcome.UNKNOWN:
            # 提交阶段未知：进入待核对，并置故障 latch。
            record.phase = TerminalPhase.UNKNOWN
            record.reason = "finalize_outcome_unknown"
            self._trip_with_diagnostic("finalize_outcome_unknown", run_id=record.run_id)
            return outcome
        record.phase = TerminalPhase.FAILED
        record.reason = outcome.value
        return outcome

    async def reconcile(self, run_id: str, *, token: str, sha256: str):
        """只读核对；不写库、不重新提交。"""

        record = self.record(run_id)
        if record.phase is TerminalPhase.COMMITTED:
            return ReconcileOutcome.CONFIRMED
        result = await self.repository.reconcile_finalize(
            run_id, token=token, sha256=sha256
        )
        outcome = result.outcome
        if outcome is ReconcileOutcome.CONFIRMED:
            record.phase = TerminalPhase.COMMITTED
            record.reason = None
            if self.latch.reason == "finalize_outcome_unknown" and not any(
                item.phase is TerminalPhase.UNKNOWN for item in self._records.values()
            ):
                # 共享 latch 必须等所有已知未知终态得到核对后才能解除。
                self.latch.clear("finalize_outcome_unknown")
        elif outcome is ReconcileOutcome.FOREIGN_TERMINAL:
            record.phase = TerminalPhase.COMMITTED
            record.reason = "foreign_terminal"
        elif outcome is ReconcileOutcome.CONTENT_CONFLICT:
            record.phase = TerminalPhase.UNKNOWN
            record.reason = "finalize_content_conflict"
            self._trip_with_diagnostic("finalize_content_conflict", run_id=run_id)
        elif outcome is ReconcileOutcome.STILL_PENDING:
            record.phase = TerminalPhase.UNKNOWN
            record.reason = "finalize_pending_review"
            self._trip_with_diagnostic("finalize_pending_review", run_id=run_id)
        else:  # UNAVAILABLE
            record.phase = TerminalPhase.UNKNOWN
            record.reason = "finalize_reconcile_unavailable"
            self._trip_with_diagnostic("finalize_reconcile_unavailable", run_id=run_id)
        return outcome

    async def shutdown(self, timeout: float | None = None) -> tuple[str, ...]:
        """等待已登记的收尾任务；超时返回未完成任务，不静默丢弃。"""

        self._closed = True
        pending = [task for task in self._tasks.values() if not task.done()]
        if not pending:
            return ()
        done, still = await asyncio.wait(
            pending, timeout=self.shutdown_timeout if timeout is None else timeout
        )
        for task in done:
            if not task.cancelled():
                task.exception()
        return tuple(task.get_name().removeprefix("finalize:") for task in still)
