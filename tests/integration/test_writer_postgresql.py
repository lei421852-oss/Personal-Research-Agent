"""真实 PostgreSQL 上的 Writer + 协调器生命周期验证（需显式授权）。

与 2.2 的 Repository 集成测试共用门控，但使用独立的目标库或独立 run 前缀。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import (
    PersistenceLatch,
    TerminalCoordinator,
    TerminalPhase,
)
from app.persistence.database import DatabaseResource
from app.persistence.repository import EventCommand, FinalizeOutcome, ResearchRepository
from app.persistence.serialization import build_finalize_command, build_report_command
from app.persistence.writer import AppendEvents, RunWriter, WriterFailed

AUTHORIZED = (
    os.environ.get("H2_TEST_PERSISTENCE_AUTHORIZED", "").strip().lower() == "yes"
)
URL = os.environ.get("H2_TEST_PERSISTENCE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not (AUTHORIZED and URL),
    reason="需要 H2_TEST_PERSISTENCE_AUTHORIZED=yes 与 H2_TEST_PERSISTENCE_DATABASE_URL",
)

TOKEN = "b" * 32
REPORT_TOKEN = "c" * 32
FINISHED = datetime(2026, 10, 25, 0, 31, 12, 184203, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def resource() -> DatabaseResource:
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


async def cleanup(res, run_ids):
    async with res.engine.begin() as connection:
        for run_id in run_ids:
            for table in ("research_events", "research_reports"):
                await connection.execute(
                    text(f"DELETE FROM {table} WHERE run_id = :r"), {"r": run_id}
                )
            await connection.execute(
                text("DELETE FROM research_runs WHERE run_id = :r"), {"r": run_id}
            )


def event(seq: int):
    return EventCommand.build(
        seq=seq,
        node_type="MainAgent",
        node_id="aggregate",
        status="running",
        event_type="progress",
        source="live",
        summary=f"e{seq}",
    )


async def create_run(repo, run_id, fingerprint):
    return await repo.create_run(
        run_id=run_id,
        mode="live",
        request_payload={
            "question": "q",
            "paper_ids": [],
            "language_mode": "auto",
            "max_model_calls": 200,
        },
        request_hash="1" * 64,
        owner_instance_id="0" * 32,
        fingerprint=fingerprint,
    )


async def with_stack(body):
    res = resource()
    await res.start()
    try:
        return await body(ResearchRepository(res), res)
    finally:
        await res.close()


def test_writer_appends_serially_and_barrier_reports_watermark():
    async def body(repo, res):
        run_id = "1" * 32
        try:
            await create_run(repo, run_id, "a" * 64)
            latch = PersistenceLatch()
            writer = RunWriter(repo, latch=latch, owner_instance_id="0" * 32)
            writer.start()
            try:
                for seq in (1, 2, 3):
                    writer.submit(
                        AppendEvents(
                            run_id=run_id,
                            events=(event(seq),),
                            expected_last_seq=seq - 1,
                        )
                    )
                result = await writer.flush()
                events = await repo.get_events(run_id)
                return result.last_event_seq, [item["event_id"] for item in events]
            finally:
                await writer.close()
        finally:
            await cleanup(res, [run_id])

    watermark, seqs = run(with_stack(body))
    assert watermark == 3
    assert seqs == [1, 2, 3]


def test_finalize_commits_report_events_and_terminal_atomically():
    async def body(repo, res):
        run_id = "2" * 32
        try:
            await create_run(repo, run_id, "b" * 64)
            latch = PersistenceLatch()
            writer = RunWriter(repo, latch=latch, owner_instance_id="0" * 32)
            writer.start()
            writer.submit(
                AppendEvents(
                    run_id=run_id, events=(event(1), event(2)), expected_last_seq=0
                )
            )
            await writer.flush()

            report = build_report_command(
                expected_current_revision=None,
                revision_token=REPORT_TOKEN,
                schema_version=1,
                snapshot_kind="answer_first",
                public_payload={"status": "completed", "answer_status": "answered"},
            )
            command = build_finalize_command(
                run_id=run_id,
                finalize_token=TOKEN,
                expected_state_version=1,
                status="completed",
                finished_at=FINISHED,
                error=None,
                metrics={"model_calls": 2},
                expected_last_event_seq=2,
                final_last_event_seq=2,
                expected_current_revision=None,
                report=report,
            )
            coordinator = TerminalCoordinator(repo, writer=writer, latch=latch)
            outcome = await coordinator.finalize(command, expected_owner_id="0" * 32)
            record = await repo.get_run(run_id)
            current = await repo.get_current_report(run_id)
            reconcile = await coordinator.reconcile(
                run_id, token=TOKEN, sha256=command.finalize_sha256
            )
            await writer.close()
            return (
                outcome,
                record.status,
                record.current_report_revision,
                current.revision,
                reconcile,
            )
        finally:
            await cleanup(res, [run_id])

    outcome, status, pointer, revision, reconcile = run(with_stack(body))
    assert outcome is FinalizeOutcome.NEW
    assert status == "completed"
    assert pointer == 1 and revision == 1
    assert reconcile.value == "confirmed"


class _Injector:
    def __init__(self):
        self._mode: str | None = None

    def arm(self, mode: str) -> None:
        self._mode = mode

    def consume(self) -> str | None:
        mode, self._mode = self._mode, None
        return mode


class _TxnProxy:
    def __init__(self, inner, injector):
        self._inner = inner
        self._injector = injector

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        mode = self._injector.consume() if exc_type is None else None
        if mode == "pre":
            raise OperationalError("lost before commit", None, Exception("lost"))
        result = await self._inner.__aexit__(exc_type, exc, tb)
        if mode == "post":
            raise OperationalError("ack lost", None, Exception("lost"))
        return result


class _SessionProxy:
    def __init__(self, inner, injector):
        self._inner = inner
        self._injector = injector

    def begin(self):
        return _TxnProxy(self._inner.begin(), self._injector)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ResourceProxy:
    def __init__(self, inner, injector):
        self._inner = inner
        self._injector = injector

    @asynccontextmanager
    async def session(self):
        async with self._inner.session() as session:
            yield _SessionProxy(session, self._injector)


def _command(run_id, version, seq):
    return build_finalize_command(
        run_id=run_id,
        finalize_token=TOKEN,
        expected_state_version=version,
        status="cancelled",
        finished_at=FINISHED,
        error={"code": "cancelled", "message": "x"},
        metrics={},
        expected_last_event_seq=seq,
        final_last_event_seq=seq,
        expected_current_revision=None,
    )


def test_ack_lost_keeps_unknown_then_reconcile_confirms_once():
    injector = _Injector()

    async def body(repo, res):
        run_id = "3" * 32
        try:
            await create_run(repo, run_id, "c" * 64)
            latch = PersistenceLatch()
            proxied = ResearchRepository(_ResourceProxy(res, injector))
            coordinator = TerminalCoordinator(proxied, latch=latch)
            command = _command(run_id, 0, 0)

            injector.arm("post")
            outcome = await coordinator.finalize(command, expected_owner_id="0" * 32)
            phase = coordinator.record(run_id).phase

            reconcile = await coordinator.reconcile(
                run_id, token=TOKEN, sha256=command.finalize_sha256
            )
            async with res.engine.connect() as connection:
                reports = (
                    await connection.execute(
                        text("SELECT count(*) FROM research_reports WHERE run_id = :r"),
                        {"r": run_id},
                    )
                ).scalar_one()
            return outcome, phase, reconcile.value, reports
        finally:
            await cleanup(res, [run_id])

    outcome, phase, reconcile, reports = run(with_stack(body))
    assert outcome is FinalizeOutcome.UNKNOWN
    assert phase is TerminalPhase.UNKNOWN
    assert reconcile == "confirmed"
    assert reports == 0  # 无报告终态不应产生报告行


def test_uncommitted_finalize_stays_pending_without_duplicate():
    injector = _Injector()

    async def body(repo, res):
        run_id = "4" * 32
        try:
            await create_run(repo, run_id, "d" * 64)
            latch = PersistenceLatch()
            proxied = ResearchRepository(_ResourceProxy(res, injector))
            coordinator = TerminalCoordinator(proxied, latch=latch)
            command = _command(run_id, 0, 0)

            injector.arm("pre")
            outcome = await coordinator.finalize(command, expected_owner_id="0" * 32)
            reconcile = await coordinator.reconcile(
                run_id, token=TOKEN, sha256=command.finalize_sha256
            )
            record = await repo.get_run(run_id)
            return outcome, reconcile.value, record.status, latch.tripped
        finally:
            await cleanup(res, [run_id])

    outcome, reconcile, status, latched = run(with_stack(body))
    assert outcome is FinalizeOutcome.UNKNOWN
    assert reconcile == "still_pending"
    assert status in {"queued", "running"}
    assert latched is True


def test_writer_failure_does_not_produce_durable_completed():
    async def body(repo, res):
        run_id = "5" * 32
        try:
            await create_run(repo, run_id, "e" * 64)
            latch = PersistenceLatch()
            writer = RunWriter(repo, latch=latch, owner_instance_id="0" * 32)
            writer.start()
            try:
                # 期望游标错误：制造冲突，Writer 必须失败而不是吞掉。
                writer.submit(
                    AppendEvents(run_id=run_id, events=(event(1),), expected_last_seq=5)
                )
                with pytest.raises(WriterFailed):
                    await writer.flush()
            finally:
                await writer.close()
            record = await repo.get_run(run_id)
            return record.status, latch.tripped
        finally:
            await cleanup(res, [run_id])

    status, latched = run(with_stack(body))
    assert status == "queued"  # 没有被伪装成 completed
    assert latched is True
