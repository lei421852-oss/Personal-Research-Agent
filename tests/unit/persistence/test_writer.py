"""有界串行 Writer 的确定性测试：容量、屏障、重试、latch 与关闭。"""

from __future__ import annotations

import asyncio

import pytest

from app.persistence.coordinator import PersistenceLatch
from app.persistence.errors import StorageError
from app.persistence.repository import (
    AppendResult,
    ConflictError,
    EventCommand,
    ReportRecord,
    RepositoryError,
)
from app.persistence.serialization import build_report_command
from app.persistence.writer import (
    AppendEvents,
    RunWriter,
    SaveReport,
    WriterClosed,
    WriterFailed,
    WriterFull,
)

RUN = "a" * 32
REPORT_TOKEN = "c" * 32


class FakeRepository:
    def __init__(self):
        self.appends: list[list[int]] = []
        self.reports: list[int] = []
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.appended = asyncio.Event()
        self.fail_times = 0
        self.error: BaseException | None = None
        self.retryable = True

    async def append_events(self, run_id, events, *, expected_last_seq):
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_times > 0:
            self.fail_times -= 1
            if self.error is not None:
                raise self.error
            raise StorageError("storage_unavailable", retryable=self.retryable)
        seqs = [event.seq for event in events]
        self.appends.append(seqs)
        self.appended.set()
        return AppendResult(tuple(seqs), (), max(seqs))

    async def save_report_revision(self, run_id, command):
        self.reports.append(command.revision)
        return ReportRecord(
            run_id=run_id,
            revision=command.revision,
            revision_token=command.revision_token,
            payload_sha256=command.payload_sha256,
            public_payload=command.public_payload,
        )


def event(seq: int):
    return EventCommand.build(
        seq=seq,
        node_type="MainAgent",
        node_id="aggregate",
        status="running",
        event_type="progress",
        source="live",
        summary=f"event-{seq}",
    )


def append(seq: int, expected: int) -> AppendEvents:
    return AppendEvents(run_id=RUN, events=(event(seq),), expected_last_seq=expected)


def run(coro):
    return asyncio.run(coro)


def test_events_are_serial_and_barrier_reports_high_watermark():
    async def body():
        repository = FakeRepository()
        writer = RunWriter(repository, latch=PersistenceLatch())
        writer.start()
        try:
            writer.submit(append(1, 0))
            writer.submit(append(2, 1))
            writer.submit(append(3, 2))
            result = await writer.flush()
            return repository.appends, result, writer.stats
        finally:
            await writer.close()

    appends, result, stats = run(body())
    assert appends == [[1], [2], [3]]
    assert result.last_event_seq == 3
    assert stats.completed == 3


def test_capacity_is_enforced_and_trips_latch():
    async def body():
        repository = FakeRepository()
        repository.gate = asyncio.Event()
        latch = PersistenceLatch()
        writer = RunWriter(repository, latch=latch, capacity=2)
        writer.start()
        try:
            writer.submit(append(1, 0))
            await repository.started.wait()  # worker 已取走第一条并阻塞在 gate 上
            writer.submit(append(2, 1))
            writer.submit(append(3, 2))
            with pytest.raises(WriterFull):
                writer.submit(append(4, 3))
            return latch.tripped, latch.reason, writer.pending
        finally:
            repository.gate.set()
            await writer.close()

    tripped, reason, pending = run(body())
    assert tripped is True
    assert reason == "writer_queue_full"
    assert pending == 2


def test_retryable_storage_error_is_retried_once():
    async def body():
        repository = FakeRepository()
        repository.fail_times = 1
        writer = RunWriter(repository, latch=PersistenceLatch(), max_attempts=2)
        writer.start()
        try:
            writer.submit(append(1, 0))
            await writer.flush()
            return repository.appends, writer.stats.retried, writer.failure
        finally:
            await writer.close()

    appends, retried, failure = run(body())
    assert appends == [[1]]
    assert retried == 1
    assert failure is None


def test_non_retryable_error_fails_flush_and_latches():
    async def body():
        repository = FakeRepository()
        repository.fail_times = 1
        repository.retryable = False
        latch = PersistenceLatch()
        writer = RunWriter(repository, latch=latch, max_attempts=2)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            return repository.appends, writer.stats.retried, latch.tripped
        finally:
            await writer.close()

    appends, retried, tripped = run(body())
    assert appends == []
    assert retried == 0
    assert tripped is True


def test_conflict_is_never_retried():
    async def body():
        repository = FakeRepository()
        repository.fail_times = 1
        repository.error = ConflictError("duplicate_active_fingerprint")
        latch = PersistenceLatch()
        writer = RunWriter(repository, latch=latch, max_attempts=2)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            return writer.stats.retried, latch.reason
        finally:
            await writer.close()

    retried, reason = run(body())
    assert retried == 0
    assert reason is not None and reason.startswith("writer_write_failed")


def test_barrier_waits_for_inflight_write():
    async def body():
        repository = FakeRepository()
        repository.gate = asyncio.Event()
        writer = RunWriter(repository, latch=PersistenceLatch())
        writer.start()
        try:
            writer.submit(append(1, 0))
            barrier = asyncio.create_task(writer.flush())
            await asyncio.sleep(0)
            blocked = not barrier.done()
            repository.gate.set()
            result = await barrier
            return blocked, result.last_event_seq
        finally:
            await writer.close()

    blocked, high_watermark = run(body())
    assert blocked is True
    assert high_watermark == 1


def test_close_preempts_queued_data():
    async def body():
        repository = FakeRepository()
        repository.gate = asyncio.Event()
        writer = RunWriter(repository, latch=PersistenceLatch(), capacity=8)
        writer.start()
        writer.submit(append(1, 0))
        await repository.started.wait()
        for seq in range(2, 6):
            writer.submit(append(seq, seq - 1))
        repository.gate.set()
        await repository.appended.wait()
        await writer.close()
        return repository.appends, writer.stats.dropped, writer.closed

    appends, dropped, closed = run(body())
    assert closed is True
    assert appends == [[1]]  # 只完成了在途的那一条
    assert dropped >= 1


def test_closed_writer_rejects_new_commands():
    async def body():
        repository = FakeRepository()
        writer = RunWriter(repository, latch=PersistenceLatch())
        writer.start()
        await writer.close()
        with pytest.raises(WriterClosed):
            writer.submit(append(1, 0))

    run(body())


def test_report_commands_are_serialized_too():
    async def body():
        repository = FakeRepository()
        writer = RunWriter(repository, latch=PersistenceLatch())
        writer.start()
        try:
            report = build_report_command(
                expected_current_revision=None,
                revision_token=REPORT_TOKEN,
                schema_version=1,
                snapshot_kind="answer_first",
                public_payload={"status": "completed"},
            )
            writer.submit(SaveReport(run_id=RUN, command=report))
            await writer.flush()
            return repository.reports
        finally:
            await writer.close()

    assert run(body()) == [1]


def test_latched_writer_rejects_submissions():
    async def body():
        repository = FakeRepository()
        latch = PersistenceLatch()
        latch.trip("persistence_latched")
        writer = RunWriter(repository, latch=latch)
        writer.start()
        try:
            with pytest.raises(RepositoryError):
                writer.submit(append(1, 0))
        finally:
            await writer.close()

    run(body())
