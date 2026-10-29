"""真实 PostgreSQL 上的 Manager 端到端：创建 → 事件 → 报告 → 终态 → 重建读取。

需 H2_TEST_PERSISTENCE_AUTHORIZED=yes 与 H2_TEST_PERSISTENCE_DATABASE_URL。
使用 Fake Runner，不调用任何付费 Provider。
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import PersistenceLatch, TerminalCoordinator
from app.persistence.database import DatabaseResource
from app.persistence.lifecycle import RunPersistence
from app.persistence.repository import ResearchRepository
from app.research.run_manager import MainAgentRunRequest, ResearchRunManager

AUTHORIZED = (
    os.environ.get("H2_TEST_PERSISTENCE_AUTHORIZED", "").strip().lower() == "yes"
)
URL = os.environ.get("H2_TEST_PERSISTENCE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not (AUTHORIZED and URL),
    reason="需要 H2_TEST_PERSISTENCE_AUTHORIZED=yes 与 H2_TEST_PERSISTENCE_DATABASE_URL",
)

PAPERS = ["clip-x", "blip-y"]


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

    async def run(self, request, sink):
        sink(
            node_type="MainAgent",
            node_id="main",
            status="running",
            event_type="started",
            summary="开始",
            details={},
        )
        sink(
            node_type="MainAgent",
            node_id="main",
            status="completed",
            event_type="completed",
            summary="结束",
            details={},
        )
        return {
            "entry_kind": "react",
            "answer_first": {
                "status": "completed",
                "answer_status": "answered",
                "markdown": "# answer",
                "citations": [],
                "metrics": {"model_calls": 2},
            },
            "status": "completed",
        }


def resource() -> DatabaseResource:
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


async def cleanup(res, run_id):
    async with res.engine.begin() as connection:
        for table in ("research_events", "research_reports"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE run_id = :r"), {"r": run_id}
            )
        await connection.execute(
            text("DELETE FROM research_runs WHERE run_id = :r"), {"r": run_id}
        )


async def cleanup_all(res):
    """一次性测试库：整体清空，避免失败用例留下活跃记录污染后续。"""

    async with res.engine.begin() as connection:
        for table in ("research_events", "research_reports", "research_runs"):
            await connection.execute(text(f"DELETE FROM {table}"))


async def wait_finalized(mgr, run_id, timeout=10.0):
    state = mgr._runs[run_id]
    # 取消路径下研究任务以 CancelledError 结束，这里只等它真正退出。
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(state.task), timeout)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
        if state.finalize_task is None:
            continue
        if state.finalize_task.done():
            return state
    raise AssertionError("finalize task did not finish")


def build_manager(lifecycle):
    return ResearchRunManager(
        FakeRunner(), FakeDependencies(), FakeReplays(), lifecycle=lifecycle
    )


def test_completed_run_is_persisted_and_readable_after_restart():
    async def body():
        res = resource()
        await res.start()
        repo = ResearchRepository(res)
        latch = PersistenceLatch()
        coordinator = TerminalCoordinator(repo, latch=latch)
        lifecycle = RunPersistence(
            repo, owner_instance_id="0" * 32, coordinator=coordinator, latch=latch
        )
        mgr = build_manager(lifecycle)
        await cleanup_all(res)
        created = await mgr.create(MainAgentRunRequest(question="q", paper_ids=PAPERS))
        run_id = created["run_id"]
        state = await wait_finalized(mgr, run_id)
        await mgr.close()
        await res.close()

        # 重建资源，模拟进程重启后的读取。
        res2 = resource()
        await res2.start()
        repo2 = ResearchRepository(res2)
        record = await repo2.get_run(run_id)
        events = await repo2.get_events(run_id)
        report = await repo2.get_current_report(run_id)
        try:
            return (
                state.persistence_error,
                record.status,
                record.current_report_revision,
                record.finalize_sha256 is not None,
                [item["event_id"] for item in events],
                report.revision if report else None,
            )
        finally:
            await cleanup(res2, run_id)
            await res2.close()

    error, status, pointer, has_hash, seqs, revision = asyncio.run(body())
    assert error is None
    assert status == "completed"
    assert pointer == 1
    assert has_hash is True
    assert seqs == [1, 2]
    assert revision == 1


def test_cancelled_run_persists_cancelled_terminal():
    class BlockingRunner(FakeRunner):
        def __init__(self):
            self.started = asyncio.Event()

        async def run(self, request, sink):
            sink(
                node_type="MainAgent",
                node_id="main",
                status="running",
                event_type="started",
                summary="开始",
                details={},
            )
            self.started.set()
            await asyncio.Event().wait()

    async def body():
        res = resource()
        await res.start()
        repo = ResearchRepository(res)
        latch = PersistenceLatch()
        coordinator = TerminalCoordinator(repo, latch=latch)
        lifecycle = RunPersistence(
            repo, owner_instance_id="0" * 32, coordinator=coordinator, latch=latch
        )
        runner = BlockingRunner()
        mgr = ResearchRunManager(
            runner, FakeDependencies(), FakeReplays(), lifecycle=lifecycle
        )
        await cleanup_all(res)
        created = await mgr.create(
            MainAgentRunRequest(question="cancel-me", paper_ids=PAPERS)
        )
        run_id = created["run_id"]
        await runner.started.wait()
        await mgr.cancel(run_id)
        state = await wait_finalized(mgr, run_id)
        await mgr.close()
        record = await repo.get_run(run_id)
        try:
            return state.persistence_error, record.status, record.error
        finally:
            await cleanup(res, run_id)
            await res.close()

    error, status, body_error = asyncio.run(body())
    assert error is None
    assert status == "cancelled"
    assert body_error["code"] == "cancelled"
