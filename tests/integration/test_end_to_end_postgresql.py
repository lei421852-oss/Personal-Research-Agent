"""Batch D：真实 PostgreSQL 上的端到端验收（场景 A–I）。

全部通过 HTTP 路由 + 真实 Repository/Writer/Coordinator/Lifecycle + Fake Runner，
不使用付费 Provider，不使用 SQLite 或 Fake Session。数据库断言走**独立连接**，
避免与应用的连接池混在一起。
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import PersistenceLatch, TerminalCoordinator
from app.persistence.database import DatabaseResource
from app.persistence.errors import StorageError
from app.persistence.lifecycle import RunPersistence
from app.persistence.repository import (
    FinalizeOutcome,
    FinalizeResult,
    ResearchRepository,
)
from app.persistence.serialization import report_content_hash, sha256_hex
from app.research.run_manager import ResearchRunManager
from app.workbench import create_app

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
REPORT = {
    "status": "completed",
    "answer_status": "answered",
    "execution_mode": "multi_agent_research",
    "markdown": "# 结论\n\n- 跨论文比较。\n- 数值 0.1 / 2.0。",
    "citations": [{"paper_id": "clip-x", "page": 3, "quote": "视觉编码器"}],
    "metrics": {"model_calls": 5},
    "unicode": "中文·🎯",
}

TERMINAL = {"completed", "failed", "cancelled"}


# ---- 测试替身 -----------------------------------------------------------


class FakeDependencies:
    def papers(self):
        return [{"paper_id": paper_id} for paper_id in PAPERS]

    def health(self):
        return {"real_research_ready": True, "language_adapter_ready": True}


class FakeReplays:
    def list_cases(self):
        return []


class ReportingRunner:
    main_agent_entry = False

    def __init__(self):
        self.calls = 0

    async def run(self, request, sink):
        self.calls += 1
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
                details={"step": index},
            )
        return {
            "entry_kind": "react",
            "answer_first": dict(REPORT),
            "status": "completed",
        }


class FailingRunner(ReportingRunner):
    async def run(self, request, sink):
        self.calls += 1
        raise RuntimeError("runner exploded")


class BlockingRunner(ReportingRunner):
    async def run(self, request, sink):
        self.calls += 1
        await asyncio.Event().wait()


class UnknownOnceRepository:
    """终态已提交但确认丢失：真实写入后对上层报告 UNKNOWN。"""

    def __init__(self, inner):
        self.inner = inner
        self.unknown_once = True
        self.finalize_calls = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def finalize_run(self, command, *, trailing_events=()):
        self.finalize_calls += 1
        result = await self.inner.finalize_run(command, trailing_events=trailing_events)
        if self.unknown_once and result.outcome is FinalizeOutcome.NEW:
            self.unknown_once = False
            return FinalizeResult(FinalizeOutcome.UNKNOWN)
        return result


class _StartFailingRepository:
    """只在创建阶段注入故障，其余调用透传。"""

    def __init__(self, inner, *, after_write: bool):
        self.inner = inner
        self.after_write = after_write

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def create_run(self, **kwargs):
        if not self.after_write:
            raise StorageError("storage_unavailable", retryable=True)
        await self.inner.create_run(**kwargs)
        # 记录已提交，但调用方无法确认结果。
        raise StorageError("storage_unavailable", retryable=True)


# ---- 装配与观测工具 -----------------------------------------------------


def resource(url: str | None = None, *, connect_timeout: float = 5) -> DatabaseResource:
    return DatabaseResource(
        DatabaseSettings(
            enabled=True,
            url=SecretStr(url or URL),
            connect_timeout=connect_timeout,
        )
    )


def lifecycle_for(res, repository=None, owner="0" * 32):
    repository = repository or ResearchRepository(res)
    latch = PersistenceLatch()
    coordinator = TerminalCoordinator(repository, latch=latch)
    return (
        RunPersistence(
            repository,
            owner_instance_id=owner,
            coordinator=coordinator,
            latch=latch,
        ),
        coordinator,
        repository,
    )


def app_for(res, runner, *, lifecycle=None):
    """把真实持久化栈挂在 HTTP 应用上；manager 在应用事件循环内构造。"""

    def factory():
        return ResearchRunManager(
            runner,
            FakeDependencies(),
            FakeReplays(),
            lifecycle=lifecycle or lifecycle_for(res)[0],
        )

    return create_app(
        manager_factory=factory,
        database_settings=res.settings,
        database_factory=lambda _: res,
    )


def wait_terminal(api, run_id, timeout=15.0):
    """有界等待后台任务落到终态；只观察 API，不猜内部状态。"""

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = api.get(f"/api/research/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in TERMINAL:
            return last
        time.sleep(0.01)
    raise AssertionError(f"run did not reach terminal state: {last}")


def wait_status(api, run_id, expected, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = api.get(f"/api/research/runs/{run_id}").json()
        if body["status"] == expected:
            return body
        time.sleep(0.01)
    raise AssertionError(f"run did not reach {expected}")


# ---- 独立连接的数据库断言 -----------------------------------------------


def _run_sql(url, statements):
    """用一次性资源执行 SQL 并返回结果；与被测应用完全隔离。"""

    async def body():
        res = resource(url)
        await res.start()
        try:
            results = []
            async with res.engine.begin() as connection:
                for statement, params in statements:
                    result = await connection.execute(text(statement), params or {})
                    results.append(
                        result.mappings().all() if result.returns_rows else []
                    )
            return results
        finally:
            await res.close()

    return asyncio.run(body())


def count_rows(where="TRUE", url=None):
    rows = _run_sql(
        url or URL,
        [(f"SELECT count(*) AS n FROM research_runs WHERE {where}", None)],
    )
    return rows[0][0]["n"]


def db_rows(run_id, url=None):
    rows = _run_sql(
        url or URL,
        [
            ("SELECT * FROM research_runs WHERE run_id = :r", {"r": run_id}),
            (
                (
                    "SELECT revision, payload_sha256, schema_version, snapshot_kind,"
                    " public_payload, authority_payload, source_reference"
                    " FROM research_reports WHERE run_id = :r ORDER BY revision"
                ),
                {"r": run_id},
            ),
            (
                "SELECT seq FROM research_events WHERE run_id = :r ORDER BY seq",
                {"r": run_id},
            ),
        ],
    )
    run_row = dict(rows[0][0]) if rows[0] else None
    return {
        "run": run_row,
        "reports": [dict(row) for row in rows[1]],
        "seqs": [row["seq"] for row in rows[2]],
    }


def single_run_row():
    rows = _run_sql(URL, [("SELECT * FROM research_runs LIMIT 1", None)])
    return dict(rows[0][0]) if rows[0] else None


def cleanup_all(url=None):
    _run_sql(
        url or URL,
        [
            (f"DELETE FROM {table}", None)
            for table in ("research_events", "research_reports", "research_runs")
        ],
    )


@pytest.fixture(autouse=True)
def clean_database():
    cleanup_all()
    yield
    cleanup_all()


def post(api, *, key=None, question="q"):
    headers = {"Idempotency-Key": key} if key else {}
    return api.post(
        "/api/research/runs",
        json={"question": question, "paper_ids": PAPERS},
        headers=headers,
    )


def assert_single_terminal(rows):
    run = rows["run"]
    assert run is not None
    assert run["status"] == "completed"
    assert run["finished_at"] is not None
    assert run["finalize_token"] is not None
    assert run["finalize_sha256"] is not None
    assert len(rows["reports"]) == 1
    report = rows["reports"][0]
    assert run["current_report_revision"] == report["revision"]
    assert rows["seqs"] == list(range(1, run["last_event_seq"] + 1))
    # 报告内容哈希可复核。
    assert report["payload_sha256"] == report_content_hash(
        schema_version=report["schema_version"],
        snapshot_kind=report["snapshot_kind"],
        public_payload=report["public_payload"],
        authority_payload=report["authority_payload"],
        source_reference=report["source_reference"],
    )


# ---- 场景 A：正常完成 ---------------------------------------------------


def test_scenario_a_normal_completion_is_atomic_and_readable():
    res = resource()
    runner = ReportingRunner()
    with TestClient(app_for(res, runner)) as api:
        created = post(api, key="a-key")
        assert created.status_code == 202
        assert "Idempotency-Replayed" not in created.headers
        run_id = created.json()["run_id"]

        final = wait_terminal(api, run_id)
        assert final["status"] == "completed"
        assert final["persisted"] is True
        assert final["report_available"] is True
        assert final["events_count"] == 2
        assert final["finished_at"] is not None

        events = api.get(f"/api/research/runs/{run_id}/events").json()
        assert [event["event_id"] for event in events["events"]] == [1, 2]
        assert events["last_event_seq"] == 2

        report = api.get(f"/api/research/runs/{run_id}/report")
        assert report.status_code == 200
        assert report.json()["markdown"].startswith("# 结论")
        revision_header = report.headers["X-Report-Revision"]
        hash_header = report.headers["X-Report-Payload-SHA256"]
        assert runner.calls == 1

    rows = db_rows(run_id)
    assert_single_terminal(rows)
    assert revision_header == "1"
    assert hash_header == rows["reports"][0]["payload_sha256"]
    run = rows["run"]
    assert run["state_version"] >= 2
    assert run["started_at"] is not None
    assert run["current_report_revision"] == 1


# ---- 场景 B：同键重放 ---------------------------------------------------


def test_scenario_b_same_key_replay_creates_no_second_task():
    res = resource()
    runner = ReportingRunner()
    with TestClient(app_for(res, runner)) as api:
        first = post(api, key="b-key")
        assert first.status_code == 202
        run_id = first.json()["run_id"]
        wait_terminal(api, run_id)
        calls_before = runner.calls
        events_before = api.get(f"/api/research/runs/{run_id}/events").json()

        second = post(api, key="b-key")
        assert second.status_code == 200
        assert second.headers["Idempotency-Replayed"] == "true"
        assert second.json()["run_id"] == run_id
        time.sleep(0.05)
        assert runner.calls == calls_before == 1
        assert api.get(f"/api/research/runs/{run_id}/events").json() == events_before

    assert_single_terminal(db_rows(run_id))
    assert count_rows() == 1


# ---- 场景 C：同键冲突 ---------------------------------------------------


def test_scenario_c_same_key_different_request_conflicts():
    res = resource()
    runner = ReportingRunner()
    with TestClient(app_for(res, runner)) as api:
        first = post(api, key="c-key", question="q1")
        assert first.status_code == 202
        run_id = first.json()["run_id"]
        wait_terminal(api, run_id)
        calls_before = runner.calls

        conflict = post(api, key="c-key", question="q2")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_key_conflict"
        time.sleep(0.05)
        assert runner.calls == calls_before == 1

    assert count_rows() == 1


# ---- 场景 D：取消与失败 -------------------------------------------------


def test_scenario_d_cancel_and_runner_failure_persist_real_terminal():
    with TestClient(app_for(resource(), BlockingRunner())) as api:
        created = post(api, key="d-cancel")
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        wait_status(api, run_id, "running")
        assert api.post(f"/api/research/runs/{run_id}/cancel").status_code == 200
        final = wait_terminal(api, run_id)
        assert final["status"] == "cancelled"
        assert final["error"]["code"] == "cancelled"

    rows = db_rows(run_id)
    assert rows["run"]["status"] == "cancelled"
    assert rows["run"]["error"]["code"] == "cancelled"
    assert rows["run"]["finished_at"] is not None
    assert rows["reports"] == []

    failing = FailingRunner()
    with TestClient(app_for(resource(), failing)) as api:
        created = post(api, key="d-fail")
        assert created.status_code == 202
        failed_id = created.json()["run_id"]
        final = wait_terminal(api, failed_id)
        assert final["status"] == "failed"
        assert final["error"]["code"] == "execution_failure"

    failed_rows = db_rows(failed_id)
    assert failed_rows["run"]["status"] == "failed"
    assert failed_rows["run"]["error"]["code"] == "execution_failure"
    assert failed_rows["reports"] == []
    assert failing.calls == 1


# ---- 场景 E：终态已提交但确认丢失 ---------------------------------------


def test_scenario_e_lost_acknowledgement_is_reconciled_without_duplicates():
    res = resource()
    runner = ReportingRunner()
    repository = UnknownOnceRepository(ResearchRepository(res))
    lifecycle = lifecycle_for(res, repository)[0]
    with TestClient(app_for(res, runner, lifecycle=lifecycle)) as api:
        created = post(api, key="e-key")
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        final = wait_terminal(api, run_id)
        assert final["status"] == "completed"
        # 只读核对成功：仍然只有一份报告、一次终态写入。
        assert repository.finalize_calls == 1
        assert api.get(f"/api/research/runs/{run_id}/report").status_code == 200

    rows = db_rows(run_id)
    assert_single_terminal(rows)
    again = db_rows(run_id)
    assert again["run"]["finalize_token"] == rows["run"]["finalize_token"]
    assert again["run"]["finalize_sha256"] == rows["run"]["finalize_sha256"]
    assert again["run"]["state_version"] == rows["run"]["state_version"]
    assert len(again["reports"]) == 1


# ---- 场景 F：提交结果未知 -----------------------------------------------


def test_scenario_f_submission_unknown_never_retries_or_fakes_success():
    # F1：提交未落库。
    res = resource()
    adapter = _StartFailingRepository(ResearchRepository(res), after_write=False)
    lifecycle = RunPersistence(
        adapter,
        owner_instance_id="0" * 32,
        coordinator=TerminalCoordinator(adapter),
        latch=PersistenceLatch(),
    )
    runner = ReportingRunner()
    with TestClient(app_for(res, runner, lifecycle=lifecycle)) as api:
        first = post(api, key="f-key")
        assert first.status_code == 503
        assert first.json()["error"]["code"] == "submission_outcome_unknown"
        assert first.json()["error"]["retry_mode"] == "reconcile_only"
        again = post(api, key="f-key")
        assert again.status_code == 503
        assert again.json()["error"]["code"] == "submission_outcome_unknown"
        time.sleep(0.05)
        assert runner.calls == 0
    assert count_rows() == 0

    # F2：提交已落库但无法确认。
    res2 = resource()
    adapter2 = _StartFailingRepository(ResearchRepository(res2), after_write=True)
    lifecycle2 = RunPersistence(
        adapter2,
        owner_instance_id="0" * 32,
        coordinator=TerminalCoordinator(adapter2),
        latch=PersistenceLatch(),
    )
    runner2 = ReportingRunner()
    with TestClient(app_for(res2, runner2, lifecycle=lifecycle2)) as api:
        first = post(api, key="f-key-2")
        assert first.status_code == 503
        assert first.json()["error"]["code"] == "submission_outcome_unknown"
        run_id = first.json()["error"]["run_id"]
        again = post(api, key="f-key-2")
        assert again.status_code == 503
        assert again.json()["error"]["code"] == "submission_outcome_unknown"
        # 同一次提交的重放拿同一判定与同一 run_id，不另判一次。
        assert again.json()["error"]["run_id"] == run_id
        time.sleep(0.05)
        assert runner2.calls == 0

    assert count_rows() == 1
    row = single_run_row()
    assert row["run_id"] == run_id
    assert row["status"] == "queued"
    assert row["finished_at"] is None
    assert row["finalize_token"] is None
    # 不自动重试、不伪装成完成：同一 run 既无报告也无事件。
    assert db_rows(run_id)["reports"] == []


# ---- 场景 G：重启后读取 -------------------------------------------------


def test_scenario_g_restart_reads_committed_state_only():
    res = resource()
    runner = ReportingRunner()
    with TestClient(app_for(res, runner)) as api:
        created = post(api, key="g-key")
        run_id = created.json()["run_id"]
        wait_terminal(api, run_id)
        before = {
            "status": api.get(f"/api/research/runs/{run_id}").json(),
            "events": api.get(f"/api/research/runs/{run_id}/events").json(),
            "report": api.get(f"/api/research/runs/{run_id}/report").json(),
        }

    # 全新进程语义：新资源 + 新 Manager，内存完全为空。
    restarted_runner = ReportingRunner()
    with TestClient(app_for(resource(), restarted_runner)) as api:
        after = {
            "status": api.get(f"/api/research/runs/{run_id}").json(),
            "events": api.get(f"/api/research/runs/{run_id}/events").json(),
            "report": api.get(f"/api/research/runs/{run_id}/report").json(),
        }
        assert restarted_runner.calls == 0
        assert api.get("/api/research/runs/unknown").status_code == 404

    assert after["events"] == before["events"]
    assert after["report"] == before["report"]
    for key in ("status", "state_version", "events_count", "finished_at", "error"):
        assert after["status"][key] == before["status"][key]
    assert after["status"]["status"] == "completed"


# ---- 场景 H：重启后仍有未完成任务 ---------------------------------------


def test_scenario_h_orphan_record_is_reported_not_taken_over():
    leftover = "9" * 32
    created_at = datetime(2026, 10, 25, tzinfo=UTC)
    _run_sql(
        URL,
        [
            (
                (
                    "INSERT INTO research_runs ("
                    " run_id, mode, status, request_payload, request_hash,"
                    " fingerprint, effective_paper_ids, owner_instance_id,"
                    " state_version, last_event_seq, created_at, updated_at"
                    ") VALUES ("
                    " :r, 'live', 'running', CAST(:payload AS jsonb), :hash,"
                    " :fingerprint, '[]'::jsonb, :owner, 1, 0,"
                    " CAST(:created AS timestamptz), CAST(:created AS timestamptz))"
                ),
                {
                    "r": leftover,
                    "payload": '{"question": "q", "paper_ids": [], '
                    '"language_mode": "auto", "max_model_calls": 200}',
                    "hash": sha256_hex(PAYLOAD),
                    "fingerprint": sha256_hex({"fingerprint": leftover}),
                    "owner": "1" * 32,
                    "created": created_at,
                },
            )
        ],
    )

    runner = ReportingRunner()
    with TestClient(app_for(resource(), runner)) as api:
        body = api.get(f"/api/research/runs/{leftover}").json()
        # 如实反映持久化事实，但绝不接管、不重排。
        assert body["status"] == "running"
        assert body["persisted"] is True
        assert body["report_available"] is False
        assert body["details_available"] is False
        assert body["finished_at"] is None
        assert api.get(f"/api/research/runs/{leftover}/report").status_code == 409
        time.sleep(0.05)
        assert runner.calls == 0

    row = db_rows(leftover)["run"]
    assert row["status"] == "running"
    assert row["finished_at"] is None
    assert row["finalize_token"] is None
    assert row["owner_instance_id"] == "1" * 32


# ---- 场景 I：读取故障 ---------------------------------------------------


def test_scenario_i_read_failures_keep_contract_codes():
    # 数据库不可用：DSN 指向无人监听端口。
    base = make_url(URL)
    broken_url = base.set(port=1).render_as_string(hide_password=False)
    unknown_id = "a" * 32
    with TestClient(
        app_for(resource(broken_url, connect_timeout=1), ReportingRunner())
    ) as api:
        for path in (
            f"/api/research/runs/{unknown_id}",
            f"/api/research/runs/{unknown_id}/events",
            f"/api/research/runs/{unknown_id}/report",
        ):
            response = api.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "storage_unavailable"

    # 无记录 → 404；无报告 → 409；完整性不一致 → 503。
    marker = "b" * 32
    with TestClient(app_for(resource(), ReportingRunner())) as api:
        for path in (
            f"/api/research/runs/{marker}",
            f"/api/research/runs/{marker}/events",
            f"/api/research/runs/{marker}/report",
        ):
            assert api.get(path).status_code == 404

        created = post(api, key="i-key")
        run_id = created.json()["run_id"]
        wait_terminal(api, run_id)

    _run_sql(
        URL,
        [
            (
                (
                    "UPDATE research_reports SET public_payload = "
                    '\'{"markdown": "# tampered"}\'::jsonb WHERE run_id = :r'
                ),
                {"r": run_id},
            )
        ],
    )
    with TestClient(app_for(resource(), ReportingRunner())) as api:
        broken = api.get(f"/api/research/runs/{run_id}/report")
        assert broken.status_code == 503
        assert broken.json()["error"]["code"] == "run_persistence_incomplete"
        # 状态与事件不受报告内容完整性影响。
        assert api.get(f"/api/research/runs/{run_id}").status_code == 200
        assert api.get(f"/api/research/runs/{run_id}/events").status_code == 200
