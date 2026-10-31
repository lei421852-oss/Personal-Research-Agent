"""Stage 5.1.1 离线可靠性回归，不连接外部服务。"""

import asyncio
import threading

import pytest

from app.persistence import failure_diagnostics as diag
from app.persistence.coordinator import PersistenceLatch, TerminalCoordinator
from app.persistence.errors import StorageError
from app.persistence.writer import RunWriter, WriterFailed
from tests.unit.persistence.test_writer import FakeRepository, append


@pytest.mark.parametrize(
    "reason",
    [
        "finalize_cancelled",
        "finalize_outcome_unknown",
        "finalize_content_conflict",
        "finalize_pending_review",
        "finalize_reconcile_unavailable",
    ],
)
def test_coordinator_first_latch_emits_safe_diagnostic(monkeypatch, reason):
    events = []
    monkeypatch.setattr("app.persistence.coordinator.emit_failure", events.append)
    latch = PersistenceLatch()
    coordinator = TerminalCoordinator(FakeRepository(), latch=latch)
    coordinator._trip_with_diagnostic(reason, run_id="a" * 32)
    assert latch.reason == reason
    assert latch.diagnostic["internal_error_code"] == reason
    assert latch.diagnostic["run_id"] == "a" * 32
    assert len(events) == 1


def test_coordinator_does_not_replace_existing_latch(monkeypatch):
    events = []
    monkeypatch.setattr("app.persistence.coordinator.emit_failure", events.append)
    latch = PersistenceLatch()
    latch.trip("writer_first", diagnostic={"event": "writer"})
    TerminalCoordinator(FakeRepository(), latch=latch)._trip_with_diagnostic(
        "finalize_cancelled", run_id="a" * 32
    )
    assert latch.reason == "writer_first"
    assert latch.diagnostic == {"event": "writer"}
    assert events == []


def test_retry_then_recovery_is_visible_without_latch(monkeypatch):
    events = []
    monkeypatch.setattr("app.persistence.writer.emit_failure", events.append)

    async def go():
        repo = FakeRepository()
        repo.fail_times = 1
        latch = PersistenceLatch()
        writer = RunWriter(repo, latch=latch)
        writer.start()
        try:
            writer.submit(append(1, 0))
            await writer.flush()
            assert not latch.tripped
            assert [x["event"] for x in events] == [
                "persistence.write_retry",
                "persistence.write_recovered",
            ]
            assert events[0]["attempt"] == 1
            assert events[1]["recovered_attempt"] == 2
        finally:
            await writer.close()

    asyncio.run(go())


def test_retry_exhaustion_preserves_first_and_emits_retry(monkeypatch):
    events = []
    monkeypatch.setattr("app.persistence.writer.emit_failure", events.append)

    async def go():
        repo = FakeRepository()
        repo.fail_times = 2
        latch = PersistenceLatch()
        writer = RunWriter(repo, latch=latch)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert latch.tripped
            assert [x["event"] for x in events] == [
                "persistence.write_retry",
                "persistence.first_failure",
            ]
        finally:
            await writer.close()

    asyncio.run(go())


def test_coordinator_builder_failure_still_latches(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("secret")

    monkeypatch.setattr("app.persistence.coordinator.build_failure", broken)
    latch = PersistenceLatch()
    before = diag.get_diagnostics_stats()["emit_errors_total"]
    TerminalCoordinator(FakeRepository(), latch=latch)._trip_with_diagnostic(
        "finalize_cancelled", run_id="a" * 32
    )
    assert latch.reason == "finalize_cancelled"
    assert diag.get_diagnostics_stats()["emit_errors_total"] == before + 1


def test_writer_timeout_is_not_database_timeout():
    error = WriterFailed("write_timeout")
    event = diag.build_failure(
        run_id="a" * 32,
        operation="append_events",
        stage="writer_apply",
        event_type="progress",
        exc=error,
        reason="writer_failed",
    )
    assert event["category"] == "writer_timeout"


def test_slow_handler_queue_full_counts_drops(monkeypatch):
    assert diag.flush_diagnostics(3)
    started = threading.Event()
    release = threading.Event()

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(diag.LOGGER, "error", blocked)
    before = diag.get_diagnostics_stats()
    try:
        assert diag.emit_failure({"event": "first"})
        assert started.wait(2)
        for _ in range(diag._MAX_PENDING):
            assert diag.emit_failure({"event": "queued"})
        assert not diag.emit_failure({"event": "overflow"})
        after = diag.get_diagnostics_stats()
        assert after["queue_dropped_total"] == before["queue_dropped_total"] + 1
    finally:
        release.set()
        diag._EVENTS.join()
        assert diag.flush_diagnostics(3)


def test_repeated_flush_and_restart_leave_no_events(monkeypatch):
    sent = []
    monkeypatch.setattr(diag.LOGGER, "error", lambda *args: sent.append(args))
    assert diag.flush_diagnostics(3)
    before = diag.get_diagnostics_stats()
    assert diag.emit_failure({"event": "first"})
    assert diag.flush_diagnostics(3)
    assert diag.flush_diagnostics(3)
    assert diag.emit_failure({"event": "second"})
    assert diag.flush_diagnostics(3)
    after = diag.get_diagnostics_stats()
    assert len(sent) == 2
    assert diag._EVENTS.empty()
    assert after["worker_restarts_total"] >= before["worker_restarts_total"] + 1


def test_handler_exception_counted_without_raising(monkeypatch):
    assert diag.flush_diagnostics(3)
    before = diag.get_diagnostics_stats()

    def broken(*args, **kwargs):
        raise RuntimeError("not logged")

    monkeypatch.setattr(diag.LOGGER, "error", broken)
    assert diag.emit_failure({"event": "safe"})
    assert diag.flush_diagnostics(3)
    assert (
        diag.get_diagnostics_stats()["handler_errors_total"]
        == before["handler_errors_total"] + 1
    )


def test_two_retry_failures_then_recover(monkeypatch):
    events = []
    monkeypatch.setattr("app.persistence.writer.emit_failure", events.append)

    async def go():
        repo = FakeRepository()
        repo.fail_times = 2
        latch = PersistenceLatch()
        writer = RunWriter(repo, latch=latch, max_attempts=3)
        writer.start()
        try:
            writer.submit(append(1, 0))
            await writer.flush()
            assert [x["event"] for x in events] == [
                "persistence.write_retry",
                "persistence.write_retry",
                "persistence.write_recovered",
            ]
            assert [x["attempt"] for x in events[:2]] == [1, 2]
            assert events[-1]["failed_attempts"] == 2
            assert not latch.tripped
        finally:
            await writer.close()

    asyncio.run(go())


def test_retry_keeps_sqlstate_but_not_exception_message(monkeypatch):
    events = []
    monkeypatch.setattr("app.persistence.writer.emit_failure", events.append)

    async def go():
        repo = FakeRepository()
        repo.fail_times = 1
        repo.error = StorageError(
            "storage_unavailable", retryable=True, sqlstate="23514"
        )
        writer = RunWriter(repo, latch=PersistenceLatch())
        writer.start()
        try:
            writer.submit(append(1, 0))
            await writer.flush()
            assert events[0]["sqlstate"] == "23514"
            assert events[0]["category"] == "database_constraint_error"
            assert events[-1]["event"] == "persistence.write_recovered"
        finally:
            await writer.close()

    asyncio.run(go())


def test_pool_timeout_and_writer_internal_are_distinct():
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    values = [
        (PoolTimeout("secret"), "database_timeout"),
        (StorageError("storage_pool_timeout"), "database_timeout"),
        (WriterFailed("unrelated"), "writer_internal_error"),
    ]
    for error, expected in values:
        record = diag.build_failure(
            run_id="a" * 32,
            operation="append_events",
            stage="writer_apply",
            event_type="progress",
            exc=error,
            reason="writer_failed",
        )
        assert record["category"] == expected
        assert "secret" not in str(record)


def test_concurrent_flush_blocks_new_events_and_drains(monkeypatch):
    assert diag.flush_diagnostics(3)
    entered = threading.Event()
    release = threading.Event()
    outputs = []
    lock = threading.Lock()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        with lock:
            outputs.append(args)

    monkeypatch.setattr(diag.LOGGER, "error", slow)
    assert diag.emit_failure({"event": "first"})
    assert entered.wait(2)
    before = diag.get_diagnostics_stats()
    results = []
    started = threading.Barrier(3)
    stop_enqueued = threading.Event()
    original_put = diag._EVENTS.put_nowait

    def observe_put(item):
        original_put(item)
        if item is diag._STOP:
            stop_enqueued.set()

    monkeypatch.setattr(diag._EVENTS, "put_nowait", observe_put)

    def flush():
        started.wait()
        results.append(diag.flush_diagnostics(3))

    threads = [threading.Thread(target=flush) for _ in range(2)]
    for thread in threads:
        thread.start()
    started.wait()
    # 等待 STOP 放入；emit 的状态检查会与完成 STOPPING 转换的线程同步。
    assert stop_enqueued.wait(2)
    try:
        assert not diag.emit_failure({"event": "after-stop"})
        assert (
            diag.get_diagnostics_stats()["emit_errors_total"]
            == before["emit_errors_total"] + 1
        )
    finally:
        release.set()
        for thread in threads:
            thread.join(3)
    assert results == [True, True]
    assert len(outputs) == 1
    assert diag._EVENTS.empty()


def test_flush_timeout_then_worker_can_restart(monkeypatch):
    assert diag.flush_diagnostics(3)
    entered = threading.Event()
    release = threading.Event()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(diag.LOGGER, "error", blocked)
    assert diag.emit_failure({"event": "one"})
    assert entered.wait(2)
    assert not diag.flush_diagnostics(0.01)
    assert not diag.emit_failure({"event": "during-stop"})
    release.set()
    assert diag.flush_diagnostics(3)
    assert diag.emit_failure({"event": "after-restart"})
    assert diag.flush_diagnostics(3)
    assert diag._EVENTS.empty()


def test_crashed_worker_restarts_to_drain_accepted_events(monkeypatch):
    assert diag.flush_diagnostics(3)
    original = diag._consume
    failed = threading.Event()

    def crash():
        failed.set()

    monkeypatch.setattr(diag, "_consume", crash)
    assert diag.emit_failure({"event": "accepted-before-crash"})
    assert failed.wait(2)
    diag._worker.join(2)
    after_crash = diag.get_diagnostics_stats()
    monkeypatch.setattr(diag, "_consume", original)
    assert diag.flush_diagnostics(3)
    assert diag._EVENTS.empty()
    assert (
        diag.get_diagnostics_stats()["worker_restarts_total"]
        == after_crash["worker_restarts_total"] + 1
    )


def test_concurrent_coordinator_and_writer_keep_first(monkeypatch):
    from app.persistence.repository import ConflictError

    emitted = []
    monkeypatch.setattr("app.persistence.coordinator.emit_failure", emitted.append)
    monkeypatch.setattr("app.persistence.writer.emit_failure", emitted.append)
    latch = PersistenceLatch()
    coordinator = TerminalCoordinator(FakeRepository(), latch=latch)
    writer = RunWriter(FakeRepository(), latch=latch)

    async def coordinator_first():
        async def a():
            coordinator._trip_with_diagnostic(
                "finalize_outcome_unknown", run_id="a" * 32
            )

        async def b():
            writer._trip(
                "writer_write_failed:event_cursor_conflict",
                command=append(1, 0),
                exc=ConflictError("event_cursor_conflict"),
            )

        await asyncio.gather(a(), b())

    asyncio.run(coordinator_first())
    assert latch.reason == "finalize_outcome_unknown"
    assert latch.diagnostic["internal_error_code"] == "finalize_outcome_unknown"
    assert len(emitted) == 1


@pytest.mark.parametrize(
    "outcome,reason",
    [
        ("CONTENT_CONFLICT", "finalize_content_conflict"),
        ("STILL_PENDING", "finalize_pending_review"),
        ("UNAVAILABLE", "finalize_reconcile_unavailable"),
    ],
)
def test_reconcile_real_path_emits_first_latch(monkeypatch, outcome, reason):
    from app.persistence.repository import ReconcileOutcome
    from tests.unit.persistence.test_coordinator import (
        RUN,
        TOKEN,
        FakeTerminalRepository,
    )

    events = []
    monkeypatch.setattr("app.persistence.coordinator.emit_failure", events.append)

    async def go():
        repo = FakeTerminalRepository(reconciles=[getattr(ReconcileOutcome, outcome)])
        coordinator = TerminalCoordinator(repo)
        await coordinator.reconcile(RUN, token=TOKEN, sha256="0" * 64)
        assert coordinator.latch.reason == reason
        assert events[0]["internal_error_code"] == reason
        assert events[0]["operation"] == "reconcile"

    asyncio.run(go())


def test_many_emitters_are_counted_and_delivered_once(monkeypatch):
    assert diag.flush_diagnostics(3)
    received = []
    received_lock = threading.Lock()
    barrier = threading.Barrier(9)

    def record(_format, payload):
        with received_lock:
            received.append(payload)

    monkeypatch.setattr(diag.LOGGER, "error", record)
    results = []
    result_lock = threading.Lock()

    def emit_batch(index):
        barrier.wait()
        local = [
            diag.emit_failure({"event": "test", "index": index * 8 + i})
            for i in range(8)
        ]
        with result_lock:
            results.extend(local)

    threads = [threading.Thread(target=emit_batch, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert all(results) and len(results) == 64
    assert diag.flush_diagnostics(3)
    assert len(received) == 64
    assert len(set(received)) == 64


def test_worker_start_failure_is_counted_and_can_recover(monkeypatch):
    assert diag.flush_diagnostics(3)
    before = diag.get_diagnostics_stats()

    def failed_start(self):
        raise RuntimeError("private exception")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", failed_start)
        assert not diag.emit_failure({"event": "test"})
    assert (
        diag.get_diagnostics_stats()["emit_errors_total"]
        == before["emit_errors_total"] + 1
    )
    assert diag.emit_failure({"event": "after-failure"})
    assert diag.flush_diagnostics(3)


def test_stats_snapshot_is_independent():
    snapshot = diag.get_diagnostics_stats()
    before = snapshot["queue_dropped_total"]
    snapshot["queue_dropped_total"] = -100
    assert diag.get_diagnostics_stats()["queue_dropped_total"] == before


def test_actual_finalize_unknown_and_cancellation_emit_once(monkeypatch):
    from app.persistence.repository import FinalizeOutcome
    from tests.unit.persistence.test_coordinator import FakeTerminalRepository, command

    events = []
    monkeypatch.setattr("app.persistence.coordinator.emit_failure", events.append)

    async def go():
        repo = FakeTerminalRepository([FinalizeOutcome.UNKNOWN])
        coordinator = TerminalCoordinator(repo)
        result = await coordinator.finalize(command())
        assert result is FinalizeOutcome.UNKNOWN
        assert events[-1]["internal_error_code"] == "finalize_outcome_unknown"

        class Waiting(FakeTerminalRepository):
            async def finalize_run(self, cmd, trailing_events=()):
                entered.set()
                await asyncio.Event().wait()

        entered = asyncio.Event()
        other = TerminalCoordinator(Waiting())
        task = asyncio.create_task(other._run_finalize(command(), ()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert other.latch.reason == "finalize_cancelled"
        assert events[-1]["internal_error_code"] == "finalize_cancelled"
        assert events[-1]["sqlstate"] is None

    asyncio.run(go())
    assert len(events) == 2


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGKILL"])
def test_forced_termination_does_not_guarantee_logging(signal_name):
    import signal
    import subprocess
    import sys

    script = """
import threading
from app.persistence import failure_diagnostics as d
started=threading.Event()
def block(*args, **kwargs):
    started.set()
    threading.Event().wait()
d.LOGGER.error=block
d.emit_failure({'event':'persistence.first_failure'})
assert started.wait(2)
print('ready', flush=True)
threading.Event().wait()
"""
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(getattr(signal, signal_name))
        _, stderr = proc.communicate(timeout=3)
        assert proc.returncode == -getattr(signal, signal_name)
        assert "persistence.first_failure" not in stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)


def test_worker_dies_before_stop_drains_accepted_events(monkeypatch):
    assert diag.flush_diagnostics(3)
    original_consume = diag._consume
    entered = threading.Event()
    release = threading.Event()
    stop_queued = threading.Event()
    delivered = []

    def unexpected_exit():
        entered.set()
        assert release.wait(3)

    def record(_format, payload):
        delivered.append(payload)

    original_put = diag._EVENTS.put_nowait

    def observe_put(item):
        original_put(item)
        if item is diag._STOP:
            stop_queued.set()

    monkeypatch.setattr(diag.LOGGER, "error", record)
    monkeypatch.setattr(diag, "_consume", unexpected_exit)
    monkeypatch.setattr(diag._EVENTS, "put_nowait", observe_put)
    result = []
    try:
        assert diag.emit_failure({"event": "accepted"})
        assert entered.wait(2)
        thread = threading.Thread(
            target=lambda: result.append(diag.flush_diagnostics(3))
        )
        thread.start()
        assert stop_queued.wait(2)
        monkeypatch.setattr(diag, "_consume", original_consume)
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert result == [True]
        assert len(delivered) == 1
        assert diag._EVENTS.empty()
    finally:
        release.set()
        monkeypatch.setattr(diag, "_consume", original_consume)
        while not diag._EVENTS.empty():
            diag._EVENTS.get_nowait()
            diag._EVENTS.task_done()
        diag.flush_diagnostics(3)
