"""Stage 5.1.2 独立 Review 指出的离线并发回归。"""

import threading

from app.persistence import failure_diagnostics as diagnostics


def test_flush_tracks_its_worker_generation_when_new_worker_starts(monkeypatch):
    assert diagnostics.flush_diagnostics(2)
    records = []
    monkeypatch.setattr(diagnostics.LOGGER, "error", lambda *args: records.append(args))
    assert diagnostics.emit_failure({"event": "old"})

    old_joined = threading.Event()
    release = threading.Event()
    original_join = threading.Thread.join

    def delayed_join(self, timeout=None):
        result = original_join(self, timeout)
        if (
            self.name == "persistence-diagnostics"
            and threading.current_thread().name == "review-flush"
            and not self.is_alive()
            and not old_joined.is_set()
        ):
            old_joined.set()
            assert release.wait(3)
        return result

    monkeypatch.setattr(threading.Thread, "join", delayed_join)
    results = []
    thread = threading.Thread(
        name="review-flush",
        target=lambda: results.append(diagnostics.flush_diagnostics(1)),
    )
    thread.start()
    try:
        assert old_joined.wait(2)
        assert diagnostics.emit_failure({"event": "new"})
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert results == [True]
    finally:
        release.set()
        original_join(thread, 3)
        assert diagnostics.flush_diagnostics(3)
    assert len(records) == 2
    assert diagnostics._EVENTS.empty()


def test_reconcile_other_unknown_does_not_clear_shared_latch():
    import asyncio
    from dataclasses import replace

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
    )
    from tests.unit.persistence.test_coordinator import command

    class ConcurrentUnknown:
        def __init__(self):
            self.started = 0
            self.both = asyncio.Event()
            self.release = asyncio.Event()

        async def finalize_run(self, cmd, trailing_events=()):
            self.started += 1
            if self.started == 2:
                self.both.set()
            await self.release.wait()
            return FinalizeResult(FinalizeOutcome.UNKNOWN)

        async def reconcile_finalize(self, run_id, *, token, sha256):
            return ReconcileResult(ReconcileOutcome.CONFIRMED)

    async def go():
        repo = ConcurrentUnknown()
        coordinator = TerminalCoordinator(repo)
        first = command()
        second = replace(first, run_id="c" * 32)
        a = asyncio.create_task(coordinator.finalize(first))
        b = asyncio.create_task(coordinator.finalize(second))
        await repo.both.wait()
        repo.release.set()
        await asyncio.gather(a, b)
        assert coordinator.record(first.run_id).phase is TerminalPhase.UNKNOWN
        assert coordinator.record(second.run_id).phase is TerminalPhase.UNKNOWN

        await coordinator.reconcile(
            second.run_id, token=second.finalize_token, sha256=second.finalize_sha256
        )
        assert coordinator.latch.tripped
        assert coordinator.record(first.run_id).phase is TerminalPhase.UNKNOWN
        with pytest.raises(PersistenceLatched):
            await coordinator.finalize(replace(first, run_id="d" * 32))

        await coordinator.reconcile(
            first.run_id, token=first.finalize_token, sha256=first.finalize_sha256
        )
        assert not coordinator.latch.tripped

    asyncio.run(go())


def test_reconcile_cannot_clear_later_inflight_writer_failure():
    import asyncio

    import pytest

    from app.persistence.coordinator import (
        PersistenceLatch,
        PersistenceLatched,
        TerminalCoordinator,
    )
    from app.persistence.repository import (
        ConflictError,
        FinalizeOutcome,
        ReconcileOutcome,
        ReconcileResult,
    )
    from app.persistence.writer import RunWriter
    from tests.unit.persistence.test_coordinator import command
    from tests.unit.persistence.test_writer import FakeRepository, append

    class ReconcileRepo:
        async def reconcile_finalize(self, *args, **kwargs):
            return ReconcileResult(ReconcileOutcome.CONFIRMED)

    async def go():
        latch = PersistenceLatch()
        coordinator = TerminalCoordinator(ReconcileRepo(), latch=latch)
        repo = FakeRepository()
        repo.gate = asyncio.Event()
        repo.fail_times = 1
        repo.error = ConflictError("event_cursor_conflict")
        writer = RunWriter(repo, latch=latch)
        writer.start()
        try:
            writer.submit(append(1, 0))
            await repo.started.wait()
            cmd = command()
            coordinator._apply(coordinator.record(cmd.run_id), FinalizeOutcome.UNKNOWN)
            repo.gate.set()

            async def wait_failed():
                while writer.failure is None:
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_failed(), 2)
            assert writer.failure.code == "event_cursor_conflict"
            await coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
            assert latch.tripped
            with pytest.raises(PersistenceLatched):
                writer.submit(append(2, 1))
        finally:
            await writer.close()

    asyncio.run(go())
