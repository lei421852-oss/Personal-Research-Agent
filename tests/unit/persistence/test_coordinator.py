"""终态协调器的确定性测试：单飞、UNKNOWN 只读核对、latch 与关闭。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.persistence.coordinator import (
    PersistenceLatched,
    TerminalCoordinator,
    TerminalPhase,
)
from app.persistence.repository import (
    FinalizeOutcome,
    FinalizeResult,
    ReconcileOutcome,
    ReconcileResult,
    RepositoryError,
)
from app.persistence.serialization import build_finalize_command

RUN = "a" * 32
TOKEN = "b" * 32
FINISHED = datetime(2026, 10, 25, 0, 31, 12, 184203, tzinfo=UTC)


def command(token: str = TOKEN):
    return build_finalize_command(
        run_id=RUN,
        finalize_token=token,
        expected_state_version=0,
        status="completed",
        finished_at=FINISHED,
        error=None,
        metrics={},
        expected_last_event_seq=0,
        final_last_event_seq=0,
        expected_current_revision=None,
    )


class FakeTerminalRepository:
    def __init__(self, outcomes=(), reconciles=()):
        self.outcomes = list(outcomes)
        self.reconcile_outcomes = list(reconciles)
        self.finalize_calls = 0
        self.reconcile_calls = 0
        self.gate: asyncio.Event | None = None

    async def finalize_run(self, cmd, trailing_events=()):
        self.finalize_calls += 1
        if self.gate is not None:
            await self.gate.wait()
        outcome = self.outcomes.pop(0) if self.outcomes else FinalizeOutcome.NEW
        return FinalizeResult(outcome)

    async def reconcile_finalize(self, run_id, *, token, sha256):
        self.reconcile_calls += 1
        outcome = (
            self.reconcile_outcomes.pop(0)
            if self.reconcile_outcomes
            else ReconcileOutcome.STILL_PENDING
        )
        return ReconcileResult(outcome)


def run(coro):
    return asyncio.run(coro)


def test_new_commit_marks_committed():
    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.NEW])
        coordinator = TerminalCoordinator(repository)
        outcome = await coordinator.finalize(command())
        return outcome, coordinator.record(RUN).phase

    outcome, phase = run(body())
    assert outcome is FinalizeOutcome.NEW
    assert phase is TerminalPhase.COMMITTED


def test_unknown_latches_and_blocks_writes():
    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.UNKNOWN])
        coordinator = TerminalCoordinator(repository)
        first = await coordinator.finalize(command())
        with pytest.raises(PersistenceLatched):
            await coordinator.finalize(command())
        return first, coordinator.record(RUN).phase, coordinator.latch.reason

    first, phase, reason = run(body())
    assert first is FinalizeOutcome.UNKNOWN
    assert phase is TerminalPhase.UNKNOWN
    assert reason == "finalize_outcome_unknown"


def test_reconcile_confirmed_clears_latch():
    async def body():
        repository = FakeTerminalRepository(
            [FinalizeOutcome.UNKNOWN], [ReconcileOutcome.CONFIRMED]
        )
        coordinator = TerminalCoordinator(repository)
        await coordinator.finalize(command())
        outcome = await coordinator.reconcile(
            RUN, token=TOKEN, sha256=command().finalize_sha256
        )
        return outcome, coordinator.record(RUN).phase, coordinator.latch.tripped

    outcome, phase, latched = run(body())
    assert outcome is ReconcileOutcome.CONFIRMED
    assert phase is TerminalPhase.COMMITTED
    assert latched is False


def test_reconcile_still_pending_keeps_latch():
    async def body():
        repository = FakeTerminalRepository(
            [FinalizeOutcome.UNKNOWN], [ReconcileOutcome.STILL_PENDING]
        )
        coordinator = TerminalCoordinator(repository)
        await coordinator.finalize(command())
        outcome = await coordinator.reconcile(
            RUN, token=TOKEN, sha256=command().finalize_sha256
        )
        return outcome, coordinator.record(RUN).phase, coordinator.latch.tripped

    outcome, phase, latched = run(body())
    assert outcome is ReconcileOutcome.STILL_PENDING
    assert phase is TerminalPhase.UNKNOWN
    assert latched is True


def test_reconcile_unavailable_keeps_latch():
    async def body():
        repository = FakeTerminalRepository(
            [FinalizeOutcome.UNKNOWN], [ReconcileOutcome.UNAVAILABLE]
        )
        coordinator = TerminalCoordinator(repository)
        await coordinator.finalize(command())
        outcome = await coordinator.reconcile(
            RUN, token=TOKEN, sha256=command().finalize_sha256
        )
        return outcome, coordinator.record(RUN).reason

    outcome, reason = run(body())
    assert outcome is ReconcileOutcome.UNAVAILABLE
    assert reason == "finalize_reconcile_unavailable"


def test_content_conflict_keeps_unknown_and_latches():
    async def body():
        repository = FakeTerminalRepository(
            [FinalizeOutcome.UNKNOWN], [ReconcileOutcome.CONTENT_CONFLICT]
        )
        coordinator = TerminalCoordinator(repository)
        await coordinator.finalize(command())
        outcome = await coordinator.reconcile(
            RUN, token=TOKEN, sha256=command().finalize_sha256
        )
        return outcome, coordinator.record(RUN).reason

    outcome, reason = run(body())
    assert outcome is ReconcileOutcome.CONTENT_CONFLICT
    assert reason == "finalize_content_conflict"


def test_foreign_terminal_is_adopted_not_overwritten():
    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.FOREIGN_TERMINAL])
        coordinator = TerminalCoordinator(repository)
        outcome = await coordinator.finalize(command())
        return outcome, coordinator.record(RUN).phase, coordinator.latch.tripped

    outcome, phase, latched = run(body())
    assert outcome is FinalizeOutcome.FOREIGN_TERMINAL
    assert phase is TerminalPhase.COMMITTED
    assert latched is False


def test_single_flight_collapses_concurrent_finalize():
    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.NEW])
        repository.gate = asyncio.Event()
        coordinator = TerminalCoordinator(repository)
        first = asyncio.create_task(coordinator.finalize(command()))
        second = asyncio.create_task(coordinator.finalize(command()))
        await asyncio.sleep(0)
        repository.gate.set()
        results = await asyncio.gather(first, second)
        return results, repository.finalize_calls

    results, calls = run(body())
    assert calls == 1
    assert results == [FinalizeOutcome.NEW, FinalizeOutcome.NEW]


def test_token_mismatch_is_rejected_after_commit():
    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.NEW])
        coordinator = TerminalCoordinator(repository)
        await coordinator.finalize(command())
        with pytest.raises(RepositoryError) as caught:
            await coordinator.finalize(command(token="c" * 32))
        return caught.value.code

    assert run(body()) == "finalize_token_mismatch"


def test_shutdown_reports_unfinished_runs():
    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.NEW])
        repository.gate = asyncio.Event()
        coordinator = TerminalCoordinator(repository)
        task = asyncio.create_task(coordinator.finalize(command()))
        await asyncio.sleep(0)
        pending = await coordinator.shutdown(timeout=0.05)
        repository.gate.set()
        await asyncio.gather(task, return_exceptions=True)
        return pending

    assert run(body()) == (RUN,)


def test_writer_flush_failure_keeps_terminal_failed():
    class FailingWriter:
        async def flush(self, timeout=None):
            from app.persistence.writer import WriterFailed

            raise WriterFailed("flush_timeout")

    async def body():
        repository = FakeTerminalRepository([FinalizeOutcome.NEW])
        coordinator = TerminalCoordinator(repository, writer=FailingWriter())
        with pytest.raises(RepositoryError):
            await coordinator.finalize(command())
        return coordinator.record(RUN).phase, repository.finalize_calls

    phase, calls = run(body())
    assert phase is TerminalPhase.FAILED
    assert calls == 0  # 屏障失败时绝不提交终态
