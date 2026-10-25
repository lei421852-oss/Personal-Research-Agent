"""Batch C：三个 GET 的真实 PostgreSQL 只读查询验收。

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
from sqlalchemy.engine import make_url

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import PersistenceLatch, TerminalCoordinator
from app.persistence.database import DatabaseResource
from app.persistence.errors import StorageError
from app.persistence.lifecycle import RunPersistence
from app.persistence.repository import ResearchRepository
from app.persistence.serialization import report_content_hash
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

# 故意覆盖中文、emoji、浮点、整数、null、空容器与嵌套结构：
# JSONB 往返后必须仍能复算出同一个内容哈希。
RICH_REPORT = {
    "status": "completed",
    "answer_status": "answered",
    "execution_mode": "multi_agent_research",
    "markdown": "# 结论\n\n- 跨论文比较：CLIP 与 BLIP。\n- 数值 0.1 / 2.0 / 1e-07。",
    "citations": [{"paper_id": "clip-x", "page": 3, "quote": "视觉编码器"}],
    "metrics": {"model_calls": 7, "total": 123456789012, "ratio": 0.1},
    "nested": {
        "items": [1, 2.5, None, True, {"deep": []}],
        "empty_object": {},
        "none": None,
    },
    "unicode": "中文·符号 🎯·全角（）",
}


class FakeDependencies:
    def papers(self):
        return [{"paper_id": paper_id} for paper_id in PAPERS]

    def health(self):
        return {"real_research_ready": True, "language_adapter_ready": True}


class FakeReplays:
    def list_cases(self):
        return []


class ReportingRunner:
    """两条事件 + 一份内容丰富的最终报告，用于哈希与事件读取验收。"""

    main_agent_entry = False

    async def run(self, request, sink):
        for index, (status, event_type, summary) in enumerate(
            [("running", "started", "开始"), ("completed", "completed", "结束")],
            start=1,
        ):
            sink(
                node_type="MainAgent",
                node_id=f"main-{index}",
                status=status,
                event_type=event_type,
                summary=summary,
                details={"step": index, "note": f"步骤{index}"},
            )
        return {
            "entry_kind": "react",
            "answer_first": dict(RICH_REPORT),
            "status": "completed",
        }


def resource() -> DatabaseResource:
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


def build_lifecycle(res, owner="0" * 32):
    repository = ResearchRepository(res)
    latch = PersistenceLatch()
    coordinator = TerminalCoordinator(repository, latch=latch)
    return (
        RunPersistence(
            repository,
            owner_instance_id=owner,
            coordinator=coordinator,
            latch=latch,
        ),
        repository,
    )


def build_manager(lifecycle):
    return ResearchRunManager(
        ReportingRunner(), FakeDependencies(), FakeReplays(), lifecycle=lifecycle
    )


async def cleanup_all(res):
    async with res.engine.begin() as connection:
        for table in ("research_events", "research_reports", "research_runs"):
            await connection.execute(text(f"DELETE FROM {table}"))


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


async def completed_run(res):
    lifecycle, repository = build_lifecycle(res)
    mgr = build_manager(lifecycle)
    created = await mgr.create(MainAgentRunRequest(question="q", paper_ids=PAPERS))
    state = await wait_finalized(mgr, created["run_id"])
    return mgr, repository, created["run_id"], state


def test_report_hash_survives_jsonb_round_trip():
    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        try:
            mgr, repository, run_id, state = await completed_run(res)
            payload, meta = await mgr.read_report(run_id)
            record = await repository.get_current_report(run_id)
            run_record = await repository.get_run(run_id)
            recomputed = report_content_hash(
                schema_version=record.schema_version,
                snapshot_kind=record.snapshot_kind,
                public_payload=payload,
                authority_payload=record.authority_payload,
                source_reference=record.source_reference,
            )
            await mgr.close()
            return {
                "payload": payload,
                "meta": meta,
                "record": record,
                "run_record": run_record,
                "recomputed": recomputed,
                "persistence_error": state.persistence_error,
            }
        finally:
            await cleanup_all(res)
            await res.close()

    result = asyncio.run(body())
    assert result["persistence_error"] is None
    # JSONB 往返不得改变内容：否则哈希复核会变成假失败。
    assert result["payload"] == RICH_REPORT
    assert result["record"].content_matches() is True
    assert result["record"].payload_sha256 == result["recomputed"]
    assert result["meta"]["payload_sha256"] == result["record"].payload_sha256
    assert result["meta"]["revision"] == 1
    assert result["meta"]["schema_version"] == 1
    assert result["meta"]["snapshot_kind"] == "answer_first"
    # 指针与报告版本一致。
    assert result["run_record"].current_report_revision == result["record"].revision


def test_reads_survive_manager_and_resource_rebuild():
    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        mgr, _repository, run_id, _state = await completed_run(res)
        before = {
            "status": await mgr.read_status(run_id),
            "events": await mgr.read_events(run_id),
            "report": await mgr.read_report(run_id),
        }
        await mgr.close()
        # 只关连接、不清库：已提交数据必须留到重建之后再读。
        await res.close()

        rebuilt_res = resource()
        await rebuilt_res.start()
        try:
            rebuilt = build_manager(build_lifecycle(rebuilt_res)[0])
            after = {
                "status": await rebuilt.read_status(run_id),
                "events": await rebuilt.read_events(run_id),
                "report": await rebuilt.read_report(run_id),
            }
            retained = len(rebuilt._runs)
            await rebuilt.close()
        finally:
            await cleanup_all(rebuilt_res)
            await rebuilt_res.close()
        return before, after, retained

    before, after, retained = asyncio.run(body())
    assert retained == 0
    assert before["events"] == after["events"]
    assert before["report"] == after["report"]
    # 数据库为权威：终态、版本、事件高水位逐一致。
    for key in ("status", "state_version", "events_count", "finished_at"):
        assert before["status"][key] == after["status"][key]
    assert after["status"]["status"] == "completed"
    assert after["status"]["report_available"] is True
    # 内存不存的字段在重建后为 null，不拿缓存值冒充。
    assert after["status"]["answer_status"] is None


def test_events_are_contiguous_and_match_high_water():
    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        try:
            mgr, repository, run_id, _state = await completed_run(res)
            full = await mgr.read_events(run_id)
            incremental = await mgr.read_events(run_id, 1)
            record = await repository.get_run(run_id)
            await mgr.close()
            return full, incremental, record
        finally:
            await cleanup_all(res)
            await res.close()

    full, incremental, record = asyncio.run(body())
    seqs = [event["event_id"] for event in full["events"]]
    assert seqs == [1, 2]
    assert len(seqs) == len(set(seqs))
    assert full["last_event_seq"] == record.last_event_seq == 2
    assert full["next_cursor"] == 2
    assert [event["event_id"] for event in incremental["events"]] == [2]
    assert full["events"][0]["timestamp"] is not None
    assert full["events"][0]["details"] == {"step": 1, "note": "步骤1"}


def test_tampered_report_payload_is_rejected_on_real_database():
    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        try:
            mgr, _repository, run_id, _state = await completed_run(res)
            # 绕过应用直接改库，模拟已提交内容被篡改。
            async with res.engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE research_reports SET public_payload = "
                        '\'{"markdown": "# tampered"}\'::jsonb WHERE run_id = :r'
                    ),
                    {"r": run_id},
                )
            try:
                await mgr.read_report(run_id)
            except Exception as exc:  # noqa: BLE001
                code, status = exc.code, exc.status_code
            else:
                code, status = None, None
            # 状态与事件不受报告内容影响，仍应可读。
            status_body = await mgr.read_status(run_id)
            events_body = await mgr.read_events(run_id)
            await mgr.close()
            return code, status, status_body["status"], len(events_body["events"])
        finally:
            await cleanup_all(res)
            await res.close()

    code, status, run_status, event_count = asyncio.run(body())
    assert (code, status) == ("run_persistence_incomplete", 503)
    assert run_status == "completed"
    assert event_count == 2


def test_create_app_wires_persistence_only_when_database_enabled(monkeypatch):
    """默认路径：启用数据库才接持久化栈；禁用时保持纯内存。"""

    from fastapi.testclient import TestClient

    from app.research import react_runtime, workbench_dependencies
    from app.workbench import create_app

    class StubDependencies:
        def papers(self):
            return []

        def health(self):
            return {"real_research_ready": False, "language_adapter_ready": False}

    class StubRuntime:
        main_agent_entry = True

        async def run(self, request, sink):  # pragma: no cover - 不在此处跑研究
            raise AssertionError("should not run")

    monkeypatch.setattr(
        workbench_dependencies, "WorkbenchDependencies", StubDependencies
    )
    monkeypatch.setattr(
        react_runtime, "ReActWorkbenchRuntime", lambda deps: StubRuntime()
    )

    monkeypatch.setenv("H2_DATABASE_ENABLED", "true")
    monkeypatch.setenv("H2_DATABASE_URL", URL)
    with TestClient(create_app()) as client:
        manager = client.app.state.research_manager
        wired = type(manager.lifecycle).__name__
        has_coordinator = client.app.state.persistence is not None

    monkeypatch.setenv("H2_DATABASE_ENABLED", "false")
    monkeypatch.delenv("H2_DATABASE_URL", raising=False)
    with TestClient(create_app()) as client:
        disabled_lifecycle = client.app.state.research_manager.lifecycle

    assert wired == "RunPersistence"
    assert has_coordinator is True
    assert disabled_lifecycle is None


def test_reads_report_storage_unavailable_with_real_driver():
    async def body():
        res = resource()
        await res.start()
        await cleanup_all(res)
        try:
            mgr, _repository, run_id, _state = await completed_run(res)
            await mgr.close()
        finally:
            await cleanup_all(res)
            await res.close()

        # 指向无人监听的端口：真实驱动失败必须显式暴露，不得返回空列表或缓存。
        base = make_url(URL)
        broken = DatabaseResource(
            DatabaseSettings(
                enabled=True,
                url=SecretStr(base.set(port=1).render_as_string(hide_password=False)),
                connect_timeout=1,
            )
        )
        await broken.start()
        try:
            strict = build_manager(build_lifecycle(broken)[0])
            results = {}
            for name, call in (
                ("status", lambda: strict.read_status(run_id)),
                ("events", lambda: strict.read_events(run_id)),
                ("report", lambda: strict.read_report(run_id)),
            ):
                try:
                    await call()
                except StorageError as exc:
                    results[name] = exc.code
                else:
                    results[name] = None
            await strict.close()
            return results
        finally:
            await broken.close()

    results = asyncio.run(body())
    assert results == {
        "status": "storage_unavailable",
        "events": "storage_unavailable",
        "report": "storage_unavailable",
    }
