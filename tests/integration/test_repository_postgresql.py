"""真实 PostgreSQL 上的 Repository 与迁移约束验证。

判定分支已由 tests/unit/persistence/test_repository_decisions.py 离线穷举；
本文件只验证 SQL、事务、约束与提交语义。未授权或未提供 DSN 时整体 skip。

授权门控与阶段 1.1 的基础设施门控**分离**：
- H2_TEST_PERSISTENCE_AUTHORIZED=yes
- H2_TEST_PERSISTENCE_DATABASE_URL=postgresql+asyncpg://...
目标必须是可丢弃的独立临时库，库名以 personal_agent_test_ 开头。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.repository import (
    ConflictError,
    EventCommand,
    FinalizeAttempt,
    FinalizeOutcome,
    ReconcileOutcome,
    ResearchRepository,
    TransitionOutcome,
)
from app.persistence.serialization import build_finalize_command, build_report_command

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


def make_resource() -> DatabaseResource:
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


async def with_repository(body):
    resource = make_resource()
    await resource.start()
    try:
        return await body(ResearchRepository(resource), resource)
    finally:
        await resource.close()


def request_payload(question="What does CLIP optimize?"):
    return {
        "question": question,
        "paper_ids": [],
        "language_mode": "auto",
        "max_model_calls": 200,
    }


async def create(repo, *, run_id, fingerprint="f" * 64, key=None, mode="live"):
    return await repo.create_run(
        run_id=run_id,
        mode=mode,
        request_payload=request_payload(),
        request_hash="1" * 64,
        owner_instance_id="0" * 32,
        fingerprint=fingerprint,
        idempotency_key_hash=key,
    )


async def cleanup(resource, run_ids):
    async with resource.engine.begin() as connection:
        for run_id in run_ids:
            await connection.execute(
                text("DELETE FROM research_events WHERE run_id = :r"), {"r": run_id}
            )
            await connection.execute(
                text("DELETE FROM research_reports WHERE run_id = :r"), {"r": run_id}
            )
            await connection.execute(
                text("DELETE FROM research_runs WHERE run_id = :r"), {"r": run_id}
            )


# ------------------------------------------------------------------ 迁移状态


async def _schema_version(repo, resource):
    async with resource.engine.connect() as connection:
        tables = (
            await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
                )
            )
        ).first()
        if tables is None:
            pytest.skip("目标库尚未迁移：先执行 alembic upgrade head")
        version = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
    return version


def test_schema_is_migrated_to_h2_0001():
    async def body(repo, resource):
        return await _schema_version(repo, resource)

    assert run(with_repository(body)) == "h2_0001"


# ------------------------------------------------------------------ 创建与幂等


def test_create_then_idempotent_replay():
    async def body(repo, resource):
        run_id = "1" * 32
        try:
            first = await create(repo, run_id=run_id, key="k" * 64)
            second = await create(repo, run_id="2" * 32, key="k" * 64)
            return first, second
        finally:
            await cleanup(resource, [run_id, "2" * 32])

    first, second = run(with_repository(body))
    assert first.run.status == "queued"
    assert first.run.state_version == 0
    assert second.outcome.value == "replayed"
    assert second.run.run_id == first.run.run_id


def test_duplicate_active_fingerprint_is_rejected():
    async def body(repo, resource):
        try:
            await create(repo, run_id="3" * 32, fingerprint="a" * 64)
            with pytest.raises(ConflictError) as caught:
                await create(repo, run_id="4" * 32, fingerprint="a" * 64)
            return caught.value.code
        finally:
            await cleanup(resource, ["3" * 32, "4" * 32])

    assert run(with_repository(body)) == "duplicate_active_fingerprint"


def test_terminal_run_frees_fingerprint():
    async def body(repo, resource):
        first = "5" * 32
        second = "6" * 32
        try:
            await create(repo, run_id=first, fingerprint="b" * 64)
            command = build_finalize_command(
                run_id=first,
                finalize_token=TOKEN,
                expected_state_version=0,
                status="failed",
                finished_at=FINISHED,
                error={"code": "research_failed", "message": "x"},
                metrics={},
                expected_last_event_seq=0,
                final_last_event_seq=0,
                expected_current_revision=None,
            )
            outcome = (await repo.finalize_run(command)).outcome
            created = await create(repo, run_id=second, fingerprint="b" * 64)
            return outcome, created.outcome.value
        finally:
            await cleanup(resource, [first, second])

    outcome, created = run(with_repository(body))
    assert outcome is FinalizeOutcome.NEW
    assert created == "created"


def test_replay_mode_is_exempt_from_active_index():
    async def body(repo, resource):
        try:
            await create(repo, run_id="7" * 32, fingerprint="c" * 64)
            result = await create(
                repo, run_id="8" * 32, fingerprint="c" * 64, mode="replay"
            )
            return result.outcome.value
        finally:
            await cleanup(resource, ["7" * 32, "8" * 32])

    assert run(with_repository(body)) == "created"


def test_active_run_cannot_carry_error():
    async def body(repo, resource):
        run_id = "9" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="d" * 64)
            async with resource.engine.begin() as connection:
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text(
                            'UPDATE research_runs SET error = \'{"code":"x"}\' '
                            "WHERE run_id = :r"
                        ),
                        {"r": run_id},
                    )
            return True
        finally:
            await cleanup(resource, [run_id])

    assert run(with_repository(body))


# ------------------------------------------------------------------ 指针约束


def test_dangling_report_pointer_is_rejected_at_commit():
    async def body(repo, resource):
        run_id = "a" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="e" * 64)
            with pytest.raises(IntegrityError):
                async with resource.engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE research_runs SET current_report_revision = 9 "
                            "WHERE run_id = :r"
                        ),
                        {"r": run_id},
                    )
            return True
        finally:
            await cleanup(resource, [run_id])

    assert run(with_repository(body))


def test_cross_run_report_pointer_is_rejected():
    async def body(repo, resource):
        first, second = "b" * 32, "c" * 32
        try:
            await create(repo, run_id=first, fingerprint="f" * 64)
            await create(repo, run_id=second, fingerprint="0" * 64)
            report = build_report_command(
                expected_current_revision=None,
                revision_token=REPORT_TOKEN,
                schema_version=1,
                snapshot_kind="answer_first",
                public_payload={"status": "completed"},
            )
            await repo.save_report_revision(first, report)
            with pytest.raises(IntegrityError):
                async with resource.engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE research_runs SET current_report_revision = 1 "
                            "WHERE run_id = :r"
                        ),
                        {"r": second},
                    )
            return True
        finally:
            await cleanup(resource, [first, second])

    assert run(with_repository(body))


# ------------------------------------------------------------------ 事务语义


def test_transition_run_uses_version_cas():
    async def body(repo, resource):
        run_id = "d" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="1" * 64)
            ok = await repo.transition_run(
                run_id,
                expected_version=0,
                from_statuses=("queued",),
                to_status="running",
            )
            stale = await repo.transition_run(
                run_id,
                expected_version=0,
                from_statuses=("queued",),
                to_status="running",
            )
            return ok.outcome, stale.outcome, ok.run.state_version
        finally:
            await cleanup(resource, [run_id])

    ok, stale, version = run(with_repository(body))
    assert ok is TransitionOutcome.UPDATED
    assert stale is TransitionOutcome.VERSION_CONFLICT
    assert version == 1


def test_append_events_is_idempotent_and_conflict_aware():
    async def body(repo, resource):
        run_id = "e" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="2" * 64)
            first = EventCommand.build(
                seq=1,
                node_type="MainAgent",
                node_id="aggregate",
                status="running",
                event_type="started",
                source="live",
                summary="开始",
            )
            outcome = await repo.append_events(run_id, [first], expected_last_seq=0)
            repeat = await repo.append_events(run_id, [first], expected_last_seq=0)
            changed = EventCommand.build(
                seq=1,
                node_type="MainAgent",
                node_id="aggregate",
                status="running",
                event_type="started",
                source="live",
                summary="被改写",
            )
            with pytest.raises(ConflictError):
                await repo.append_events(run_id, [changed], expected_last_seq=0)
            return outcome.appended, repeat.duplicates
        finally:
            await cleanup(resource, [run_id])

    appended, duplicates = run(with_repository(body))
    assert appended == (1,)
    assert duplicates == (1,)


def test_finalize_then_reconcile_confirmed_without_extra_write():
    async def body(repo, resource):
        run_id = "f" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="3" * 64)
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
                expected_state_version=0,
                status="completed",
                finished_at=FINISHED,
                error=None,
                metrics={"model_calls": 2},
                expected_last_event_seq=0,
                final_last_event_seq=0,
                expected_current_revision=None,
                report=report,
            )
            result = await repo.finalize_run(command)
            before = await repo.get_run(run_id)
            reconcile = await repo.reconcile_finalize(
                run_id, token=TOKEN, sha256=command.finalize_sha256
            )
            after = await repo.get_run(run_id)
            return (
                result.outcome,
                reconcile.outcome,
                before.state_version,
                after.state_version,
            )
        finally:
            await cleanup(resource, [run_id])

    outcome, reconcile, before, after = run(with_repository(body))
    assert outcome is FinalizeOutcome.NEW
    assert reconcile is ReconcileOutcome.CONFIRMED
    assert before == after  # 核对不写库


def test_commit_ack_lost_maps_to_unknown_then_reconcile(monkeypatch):
    """提交已落库但确认丢失：Repository 必须返回 unknown，核对后可确认。"""

    from sqlalchemy.ext.asyncio import AsyncSession

    async def body(repo, resource):
        run_id = "0" * 31 + "1"
        try:
            await create(repo, run_id=run_id, fingerprint="4" * 64)
            command = build_finalize_command(
                run_id=run_id,
                finalize_token=TOKEN,
                expected_state_version=0,
                status="cancelled",
                finished_at=FINISHED,
                error={"code": "cancelled", "message": "x"},
                metrics={},
                expected_last_event_seq=0,
                final_last_event_seq=0,
                expected_current_revision=None,
            )
            original = AsyncSession.commit

            async def commit_then_fail(self):
                await original(self)
                raise OperationalError("commit ack lost", None, Exception("lost"))

            monkeypatch.setattr(AsyncSession, "commit", commit_then_fail)
            result = await repo.finalize_run(command)
            monkeypatch.setattr(AsyncSession, "commit", original)

            reconcile = await repo.reconcile_finalize(
                run_id, token=TOKEN, sha256=command.finalize_sha256
            )
            return result.outcome, reconcile.outcome
        finally:
            monkeypatch.undo()
            await cleanup(resource, [run_id])

    outcome, reconcile = run(with_repository(body))
    assert outcome is FinalizeOutcome.UNKNOWN
    assert reconcile is ReconcileOutcome.CONFIRMED


def test_uncommitted_finalize_stays_pending_and_is_not_rewritten(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async def body(repo, resource):
        run_id = "0" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="5" * 64)
            command = build_finalize_command(
                run_id=run_id,
                finalize_token=TOKEN,
                expected_state_version=0,
                status="failed",
                finished_at=FINISHED,
                error={"code": "research_failed", "message": "x"},
                metrics={},
                expected_last_event_seq=0,
                final_last_event_seq=0,
                expected_current_revision=None,
            )
            original = AsyncSession.commit

            async def fail_before_commit(self):
                raise OperationalError("connection lost", None, Exception("lost"))

            monkeypatch.setattr(AsyncSession, "commit", fail_before_commit)
            result = await repo.finalize_run(command)
            monkeypatch.setattr(AsyncSession, "commit", original)

            reconcile = await repo.reconcile_finalize(
                run_id, token=TOKEN, sha256=command.finalize_sha256
            )
            state = await repo.get_run(run_id)
            return result.outcome, reconcile.outcome, state.status
        finally:
            monkeypatch.undo()
            await cleanup(resource, [run_id])

    outcome, reconcile, status = run(with_repository(body))
    assert outcome is FinalizeOutcome.UNKNOWN
    assert reconcile is ReconcileOutcome.STILL_PENDING
    assert status in {"queued", "running"}  # 未被自动改写


def test_finalize_attempt_state_machine_blocks_writes_after_unknown():
    attempt = FinalizeAttempt()
    attempt.start(TOKEN)
    attempt.mark_unknown()
    assert not attempt.can_finalize
    assert attempt.must_reconcile


def test_report_rows_are_immutable_versions():
    async def body(repo, resource):
        run_id = "2" * 32
        try:
            await create(repo, run_id=run_id, fingerprint="6" * 64)
            first = build_report_command(
                expected_current_revision=None,
                revision_token="a" * 32,
                schema_version=1,
                snapshot_kind="answer_first",
                public_payload={"status": "completed", "v": 1},
            )
            second = build_report_command(
                expected_current_revision=1,
                revision_token="d" * 32,
                schema_version=1,
                snapshot_kind="answer_first",
                public_payload={"status": "completed", "v": 2},
            )
            await repo.save_report_revision(run_id, first)
            await repo.save_report_revision(run_id, second)
            async with resource.engine.connect() as connection:
                revisions = (
                    (
                        await connection.execute(
                            text(
                                "SELECT revision FROM research_reports "
                                "WHERE run_id = :r ORDER BY revision"
                            ),
                            {"r": run_id},
                        )
                    )
                    .scalars()
                    .all()
                )
            current = await repo.get_current_report(run_id)
            return list(revisions), current.revision
        finally:
            await cleanup(resource, [run_id])

    revisions, current = run(with_repository(body))
    assert revisions == [1, 2]
    assert current == 2
