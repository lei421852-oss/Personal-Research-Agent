"""真实 PostgreSQL 上的 admission 验收：唯一约束裁决、提交窗口等待、失败收尾。

需 H2_TEST_PERSISTENCE_AUTHORIZED=yes 与 H2_TEST_PERSISTENCE_DATABASE_URL。
全部使用 Fake Runner，不调用任何付费 Provider。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import PersistenceLatch, TerminalCoordinator
from app.persistence.database import DatabaseResource
from app.persistence.lifecycle import RunPersistence, key_hash
from app.persistence.repository import CreateOutcome, ResearchRepository
from app.persistence.serialization import sha256_hex
from app.research.run_manager import (
    MainAgentRunRequest,
    ResearchRunManager,
    RunManagerError,
)

AUTHORIZED = (
    os.environ.get("H2_TEST_PERSISTENCE_AUTHORIZED", "").strip().lower() == "yes"
)
URL = os.environ.get("H2_TEST_PERSISTENCE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not (AUTHORIZED and URL),
    reason="需要 H2_TEST_PERSISTENCE_AUTHORIZED=yes 与 H2_TEST_PERSISTENCE_DATABASE_URL",
)

PAPERS = ["clip-x", "blip-y"]
PAYLOAD = {
    "question": "q",
    "paper_ids": PAPERS,
    "language_mode": "auto",
    "max_model_calls": 200,
}


class FakeDependencies:
    def papers(self):
        return [{"paper_id": paper_id} for paper_id in PAPERS]

    def health(self):
        return {"real_research_ready": True, "language_adapter_ready": True}


class FakeReplays:
    def list_cases(self):
        return []


class FakeRunner:
    main_agent_entry = False

    def __init__(self):
        self.calls = 0

    async def run(self, request, sink):
        self.calls += 1
        return {"status": "completed", "research": {"status": "completed"}}


def failing_scheduler(pending, name=None):
    del pending, name
    raise RuntimeError("scheduler unavailable")


def resource() -> DatabaseResource:
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


async def cleanup_all(res):
    async with res.engine.begin() as connection:
        for table in ("research_events", "research_reports", "research_runs"):
            await connection.execute(text(f"DELETE FROM {table}"))


async def count_rows(res, where="TRUE"):
    async with res.engine.begin() as connection:
        return await connection.scalar(
            text(f"SELECT count(*) FROM research_runs WHERE {where}")
        )


async def wait_for_lock_waiter(connection, timeout=5.0):
    """在已持有的事务里轮询 pg_locks：出现未授予的锁说明插入已撞上唯一约束。

    复用持有方连接，不额外占一条连接（默认池只有 2 条）。
    """

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        waiting = await connection.scalar(
            text("SELECT count(*) FROM pg_locks WHERE NOT granted")
        )
        if waiting:
            return True
        await asyncio.sleep(0.005)
    return False


def build_repo(res):
    latch = PersistenceLatch()
    coordinator = TerminalCoordinator(ResearchRepository(res), latch=latch)
    lifecycle = RunPersistence(
        ResearchRepository(res),
        owner_instance_id="0" * 32,
        coordinator=coordinator,
        latch=latch,
    )
    return lifecycle


class WindowLifecycle:
    """在真实「已提交、尚未注册」窗口暂停，让并发请求真的经过该窗口。"""

    def __init__(self, inner):
        self.inner = inner
        self.committed = asyncio.Event()
        self.window = asyncio.Event()
        self.find_event = asyncio.Event()
        self.find_calls = 0

    async def start(self, **kwargs):
        result = await self.inner.start(**kwargs)
        # 记录已在真实 PostgreSQL 提交；此处正是 Manager 注册 Task 之前的窗口。
        self.committed.set()
        await self.window.wait()
        return result

    async def find_by_key(self, digest):
        self.find_calls += 1
        row = await self.inner.find_by_key(digest)
        self.find_event.set()
        return row

    async def mark_running(self, run_id):
        return await self.inner.mark_running(run_id)

    def on_event(self, run_id, event):
        return self.inner.on_event(run_id, event)

    async def finish(self, run_id, snapshot):
        return await self.inner.finish(run_id, snapshot)

    async def close(self):
        return await self.inner.close()


async def wait_finalized(mgr, run_id, timeout=15.0):
    state = mgr._runs[run_id]
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(state.task), timeout)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
        if state.finalize_task is not None and state.finalize_task.done():
            return state
    raise AssertionError("finalize task did not finish")


def test_unique_conflict_rolls_back_and_reads_winner_in_new_session():
    """写方先持锁未提交：对方插入必撞唯一索引，回滚后必须用新事务读胜者。"""

    async def body():
        res = resource()
        await res.start()
        repo = ResearchRepository(res)
        await cleanup_all(res)
        digest = key_hash("race-key")
        winner = "1" * 32
        loser = "2" * 32
        pending = None
        try:
            async with res.engine.connect() as holder:
                transaction = await holder.begin()
                await holder.execute(
                    text(
                        "INSERT INTO research_runs ("
                        " run_id, mode, status, request_payload, request_hash,"
                        " idempotency_key_hash, effective_paper_ids,"
                        " owner_instance_id, state_version, last_event_seq,"
                        " created_at, updated_at"
                        ") VALUES ("
                        " :run_id, 'live', 'queued', '{}'::jsonb, :request_hash,"
                        " :digest, '[]'::jsonb, :owner, 0, 0, now(), now())"
                    ),
                    {
                        "run_id": winner,
                        "request_hash": sha256_hex(PAYLOAD),
                        "digest": digest,
                        "owner": "o" * 32,
                    },
                )
                # 对方事务未提交：本次 create_run 的预检查看不见该行。
                pending = asyncio.create_task(
                    repo.create_run(
                        run_id=loser,
                        mode="live",
                        request_payload=PAYLOAD,
                        request_hash=sha256_hex(PAYLOAD),
                        owner_instance_id="0" * 32,
                        idempotency_key_hash=digest,
                        created_at=datetime.now(UTC),
                    )
                )
                blocked = await wait_for_lock_waiter(holder)
                await transaction.commit()
            result = await asyncio.wait_for(pending, 15)
            pending = None
            return (
                blocked,
                result.outcome,
                result.run.run_id if result.run else None,
                await count_rows(res),
            )
        finally:
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            await cleanup_all(res)
            await res.close()

    blocked, outcome, run_id, rows = asyncio.run(body())
    assert blocked is True
    assert outcome is CreateOutcome.REPLAYED
    assert run_id == "1" * 32
    assert rows == 1


def test_replay_waits_for_registration_across_commit_window():
    """提交已完成、Task 未注册：同键重放方必须等待，不能 409 也不能建第二个 Task。"""

    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        bridge = WindowLifecycle(build_repo(res))
        runner = FakeRunner()
        mgr = ResearchRunManager(
            runner,
            FakeDependencies(),
            FakeReplays(),
            lifecycle=bridge,
            admission_timeout=10.0,
        )
        try:
            leader = asyncio.create_task(
                mgr.create(
                    MainAgentRunRequest(question="q", paper_ids=PAPERS),
                    idempotency_key="kw",
                )
            )
            assert await asyncio.wait_for(bridge.committed.wait(), 15) is True
            bridge.find_event.clear()
            follower = asyncio.create_task(
                mgr.create(
                    MainAgentRunRequest(question="q", paper_ids=PAPERS),
                    idempotency_key="kw",
                )
            )
            # 等到重放方真正进入归属判定；它此刻只可能停在 admission 等待上。
            await asyncio.wait_for(bridge.find_event.wait(), 15)
            waited = not follower.done()
            tasks_while_waiting = sum(
                1 for r in mgr._runs.values() if r.task is not None
            )
            rows_while_waiting = await count_rows(res)
            bridge.window.set()
            replayed = await asyncio.wait_for(follower, 15)
            created = await asyncio.wait_for(leader, 15)
            state = await wait_finalized(mgr, created["run_id"])
            return (
                waited,
                tasks_while_waiting,
                rows_while_waiting,
                replayed["replayed"],
                replayed["run_id"] == created["run_id"],
                runner.calls,
                state.persistence_error,
                await count_rows(res),
            )
        finally:
            await mgr.close()
            await cleanup_all(res)
            await res.close()

    (
        waited,
        tasks_while_waiting,
        rows_while_waiting,
        replayed,
        same_run,
        calls,
        persistence_error,
        rows,
    ) = asyncio.run(body())
    assert waited is True
    assert tasks_while_waiting == 0
    assert rows_while_waiting == 1
    assert replayed is True
    assert same_run is True
    assert calls == 1
    assert persistence_error is None
    assert rows == 1


def test_scheduling_failure_is_persisted_as_terminal_failed():
    """已确认未安排：失败收尾必须落成终态 failed，且错误码可追溯。"""

    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        repo = ResearchRepository(res)
        lifecycle = build_repo(res)
        runner = FakeRunner()
        mgr = ResearchRunManager(
            runner,
            FakeDependencies(),
            FakeReplays(),
            lifecycle=lifecycle,
            scheduler=failing_scheduler,
        )
        try:
            error = None
            try:
                await mgr.create(
                    MainAgentRunRequest(question="q", paper_ids=PAPERS),
                    idempotency_key="kf",
                )
            except RunManagerError as exc:
                error = exc
            rows = await count_rows(res)
            record = await repo.get_run(error.run_id)
            return (
                error.code,
                error.status_code,
                error.retry_mode,
                runner.calls,
                rows,
                record,
            )
        finally:
            await mgr.close()
            await cleanup_all(res)
            await res.close()

    code, status, retry_mode, calls, rows, record = asyncio.run(body())
    assert code == "task_scheduling_failed"
    assert status == 500
    assert retry_mode == "safe_retry"
    assert calls == 0
    assert rows == 1
    assert record.status == "failed"
    assert record.error["code"] == "task_scheduling_failed"
    assert record.finished_at is not None
    assert record.finalize_token is not None
    assert record.finalize_sha256 is not None
