"""Batch C：三个 GET 的持久化只读查询矩阵（离线，Fake 持久化替身）。

覆盖计划 §4 的八类场景：完整数据、记录不存在、数据库不可用、仅数据库有、
仅内存有、游标边界、默认禁用、完整性不一致。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.persistence.errors import StorageError
from app.persistence.repository import ReportRecord
from app.persistence.serialization import build_report_command
from app.research.run_manager import _Run
from app.workbench import create_app
from tests.unit.research.test_manager_admission import Runner as BaseRunner
from tests.unit.research.test_manager_admission import (
    Store,
    manager,
    request,
    run,
    settle,
)

REPORT_PAYLOAD = {
    "status": "completed",
    "answer_status": "answered",
    "execution_mode": "direct",
    "markdown": "# answer",
    "citations": [],
    "metrics": {"model_calls": 2},
}


class ReportingRunner(BaseRunner):
    """产生两条真实事件并给出一份可持久化的最终报告。"""

    async def run(self, request, sink):
        self.calls += 1
        for index, (status, event_type, summary) in enumerate(
            [
                ("running", "started", "开始"),
                ("completed", "completed", "结束"),
            ],
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
            "answer_first": dict(REPORT_PAYLOAD),
            "status": "completed",
        }


class PersistentStore(Store):
    """在 admission 替身上补出读取面：事件、报告版本与读取故障注入。"""

    def __init__(self):
        super().__init__()
        self.events: dict[str, list[dict]] = {}
        self.reports: dict[tuple[str, int], ReportRecord] = {}
        self.read_error: Exception | None = None
        self.fail_read_for: set[str] = set()

    # ---- 写入面 ---------------------------------------------------------

    async def mark_running(self, run_id):
        row = self.by_run.get(run_id)
        if row is None or row.status != "queued":
            return
        # 与真实 transition_run 一致：置位 started_at 并推进 state_version。
        self.by_run[run_id] = replace(
            row,
            status="running",
            started_at=datetime.now(UTC),
            state_version=row.state_version + 1,
        )

    def on_event(self, run_id, event):
        occurred_at = (
            datetime.fromisoformat(event.timestamp) if event.timestamp else None
        )
        self.events.setdefault(run_id, []).append(
            {
                "event_id": event.event_id,
                "node_type": event.node_type,
                "node_id": event.node_id,
                "status": event.status,
                "event_type": event.event_type,
                "source": event.source,
                "summary": event.summary,
                "details": dict(event.details or {}),
                "occurred_at": occurred_at,
            }
        )
        row = self.by_run.get(run_id)
        if row is not None:
            self.by_run[run_id] = replace(row, last_event_seq=event.event_id)

    async def finish(self, run_id, snapshot):
        await super().finish(run_id, snapshot)
        row = self.by_run.get(run_id)
        report = snapshot.get("report")
        if row is None or not isinstance(report, dict) or not report:
            return
        command = build_report_command(
            expected_current_revision=row.current_report_revision,
            revision_token="b" * 32,
            schema_version=1,
            snapshot_kind="answer_first",
            public_payload=report,
        )
        self.reports[(run_id, command.revision)] = ReportRecord(
            run_id=run_id,
            revision=command.revision,
            revision_token=command.revision_token,
            payload_sha256=command.payload_sha256,
            public_payload=command.public_payload,
            schema_version=command.schema_version,
            snapshot_kind=command.snapshot_kind,
        )
        updated = replace(
            row,
            current_report_revision=command.revision,
            state_version=row.state_version + 1,
            metrics=report.get("metrics", {}),
        )
        self.by_run[run_id] = updated
        for key, value in list(self.by_key.items()):
            if value.run_id == run_id:
                self.by_key[key] = updated

    # ---- 读取面 ---------------------------------------------------------

    def _guard(self, run_id):
        if self.read_error is not None:
            raise self.read_error
        if run_id in self.fail_read_for:
            raise StorageError("storage_unavailable", retryable=True)

    async def get_run(self, run_id):
        self._guard(run_id)
        return self.by_run.get(run_id)

    async def get_events(self, run_id, *, after=0):
        self._guard(run_id)
        rows = [row for row in self.events.get(run_id, []) if row["event_id"] > after]
        return sorted(rows, key=lambda row: row["event_id"])

    async def get_current_report(self, run_id):
        self._guard(run_id)
        row = self.by_run.get(run_id)
        if row is None or row.current_report_revision is None:
            return None
        return self.reports.get((run_id, row.current_report_revision))


async def completed_run(**limits):
    """跑到终态的固定夹具：返回 (manager, runner, store, run_id)。"""

    store = PersistentStore()
    mgr, runner = manager(store, runner=ReportingRunner(), **limits)
    created = await mgr.create(request(), idempotency_key="k1")
    await settle(mgr, created["run_id"])
    return mgr, runner, store, created["run_id"]


# ---- 状态查询 -----------------------------------------------------------


def test_status_reads_committed_record_with_version_and_times():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        body = await mgr.read_status(run_id)
        record = store.by_run[run_id]
        await mgr.close()
        return body, record

    body, record = run(scenario())
    assert body["persisted"] is True
    assert body["status"] == "completed"
    assert body["state_version"] == record.state_version
    assert body["events_count"] == record.last_event_seq == 2
    assert body["finished_at"] is not None and body["started_at"] is not None
    assert body["report_available"] is True
    assert body["question"] == "q"
    assert body["paper_ids"] == ["clip-x", "blip-y"]
    # 内存仍在：数据库不存的展示字段由内存补齐。
    assert body["answer_status"] == "answered"
    assert body["metrics"] == {"model_calls": 2}


def test_status_after_restart_reads_database_only():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        await mgr.close()
        rebuilt = manager(store)[0]
        body = await rebuilt.read_status(run_id)
        retained = len(rebuilt._runs)
        await rebuilt.close()
        return body, retained

    body, retained = run(scenario())
    assert retained == 0
    assert body["persisted"] is True
    assert body["status"] == "completed"
    # 数据库不存的字段为 null，而不是拿缓存值冒充。
    assert body["answer_status"] is None
    assert body["research_question"] is None
    assert body["report_available"] is True


def test_status_unknown_run_is_404():
    async def scenario():
        store = PersistentStore()
        mgr, _ = manager(store)
        try:
            await mgr.read_status("f" * 32)
        except Exception as exc:  # noqa: BLE001
            return exc.code, exc.status_code
        return None, None

    assert run(scenario()) == ("run_not_found", 404)


def test_status_storage_failure_never_falls_back_to_memory():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        store.read_error = StorageError("storage_unavailable", retryable=True)
        try:
            await mgr.read_status(run_id)
        except StorageError as exc:
            return exc.code, exc.retryable
        finally:
            store.read_error = None
            await mgr.close()
        return None, None

    assert run(scenario()) == ("storage_unavailable", True)


def test_status_storage_failure_for_a_single_run_also_raises():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        store.fail_read_for.add(run_id)
        try:
            await mgr.read_status(run_id)
        except StorageError as exc:
            return exc.code
        finally:
            store.fail_read_for.discard(run_id)
            await mgr.close()
        return None

    assert run(scenario()) == "storage_unavailable"


def test_status_memory_run_absent_from_database_is_incomplete():
    async def scenario():
        store = PersistentStore()
        mgr, _ = manager(store)
        # 模拟「本实例认为在跑，数据库没有该记录」的完整性破坏。
        mgr._runs["a" * 32] = _Run("a" * 32, "live", "q", ["clip-x"], "2026-10-25")
        try:
            await mgr.read_status("a" * 32)
        except Exception as exc:  # noqa: BLE001
            return exc.code, exc.status_code, exc.retry_mode
        finally:
            await mgr.close()
        return None, None, None

    assert run(scenario()) == ("run_persistence_incomplete", 503, "reconcile_only")


def test_status_disabled_mode_is_unchanged():
    async def scenario():
        mgr, _runner = manager(None)
        created = await mgr.create(request())
        await settle(mgr, created["run_id"])
        body = await mgr.read_status(created["run_id"])
        await mgr.close()
        return body

    body = run(scenario())
    # 禁用持久化：与内存版 status() 完全一致，且不引入新键。
    assert body["status"] == "completed"
    assert "persisted" not in body
    assert "state_version" not in body


def test_status_replay_run_answers_from_memory():
    async def scenario():
        store = PersistentStore()
        mgr, _ = manager(store)
        identity = "c" * 32
        replay = _Run(identity, "replay", "hist", ["clip-x"], "2026-10-25")
        replay.status = "completed"
        replay.report = {"status": "completed", "answer_status": "answered"}
        replay.execution = {"research": {"status": "completed"}}
        mgr._runs[identity] = replay
        try:
            return await mgr.read_status(identity)
        finally:
            await mgr.close()

    body = run(scenario())
    # 回放不落库：仍由内存回答。
    assert body["mode"] == "replay"
    assert body["status"] == "completed"
    assert body["historical_status"] == "completed"
    assert "persisted" not in body


# ---- 事件查询 -----------------------------------------------------------


def test_events_read_committed_sequence_without_gap_or_duplicate():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        body = await mgr.read_events(run_id)
        await mgr.close()
        return body, store.by_run[run_id]

    body, record = run(scenario())
    seqs = [event["event_id"] for event in body["events"]]
    assert seqs == [1, 2]
    assert body["next_cursor"] == 2
    assert body["last_event_seq"] == record.last_event_seq == 2
    assert all(event["run_id"] == record.run_id for event in body["events"])
    assert body["events"][0]["timestamp"] is not None


def test_events_incremental_cursor():
    async def scenario():
        mgr, _runner, _store, run_id = await completed_run()
        body = await mgr.read_events(run_id, 1)
        await mgr.close()
        return body

    body = run(scenario())
    assert [event["event_id"] for event in body["events"]] == [2]
    assert body["next_cursor"] == 2


def test_events_cursor_beyond_high_water_is_422():
    async def scenario():
        mgr, _runner, _store, run_id = await completed_run()
        errors = []
        for cursor in (3, -1, True):
            try:
                await mgr.read_events(run_id, cursor)
            except Exception as exc:  # noqa: BLE001
                errors.append((exc.code, exc.status_code))
        await mgr.close()
        return errors

    assert run(scenario()) == [
        ("invalid_cursor", 422),
        ("invalid_cursor", 422),
        ("invalid_cursor", 422),
    ]


def test_events_after_restart_are_identical():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        before = await mgr.read_events(run_id)
        await mgr.close()
        rebuilt = manager(store)[0]
        after = await rebuilt.read_events(run_id)
        await rebuilt.close()
        return before, after

    before, after = run(scenario())
    assert before == after


def test_events_without_produced_events_returns_empty_with_zero_cursor():
    async def scenario():
        store = PersistentStore()
        mgr, _ = manager(store)
        created = await mgr.create(request())
        await mgr._runs[created["run_id"]].task
        body = await mgr.read_events(created["run_id"])
        await mgr.close()
        return body

    body = run(scenario())
    assert body["events"] == []
    assert body["next_cursor"] == 0


def test_events_unknown_run_is_404_and_storage_failure_is_503():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        try:
            await mgr.read_events("f" * 32)
        except Exception as exc:  # noqa: BLE001
            unknown = (exc.code, exc.status_code)
        store.read_error = StorageError("storage_unavailable", retryable=True)
        try:
            await mgr.read_events(run_id)
        except StorageError as exc:
            unavailable = exc.code
        finally:
            store.read_error = None
            await mgr.close()
        return unknown, unavailable

    unknown, unavailable = run(scenario())
    assert unknown == ("run_not_found", 404)
    assert unavailable == "storage_unavailable"


def test_events_contract_violation_is_503_not_empty_list():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        store.events[run_id][0]["node_type"] = "NotANode"
        try:
            await mgr.read_events(run_id)
        except Exception as exc:  # noqa: BLE001
            code, status = exc.code, exc.status_code
        else:
            code, status = None, None
        await mgr.close()
        return code, status

    assert run(scenario()) == ("run_persistence_incomplete", 503)


# ---- 报告查询 -----------------------------------------------------------


def test_report_reads_committed_payload_and_meta():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        payload, meta = await mgr.read_report(run_id)
        record = store.reports[(run_id, 1)]
        await mgr.close()
        return payload, meta, record

    payload, meta, record = run(scenario())
    assert payload["markdown"] == "# answer"
    assert payload["answer_status"] == "answered"
    assert meta["revision"] == record.revision == 1
    assert meta["schema_version"] == 1
    assert meta["snapshot_kind"] == "answer_first"
    assert meta["payload_sha256"] == record.payload_sha256
    assert record.content_matches() is True


def test_report_after_restart_reads_same_payload_and_hash():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        before = await mgr.read_report(run_id)
        await mgr.close()
        rebuilt = manager(store)[0]
        after = await rebuilt.read_report(run_id)
        await rebuilt.close()
        return before, after

    before, after = run(scenario())
    assert before == after


def test_report_missing_unknown_run_and_storage_failure():
    async def scenario():
        store = PersistentStore()
        mgr, _ = manager(store)
        created = await mgr.create(request())
        await mgr._runs[created["run_id"]].task
        # 未收尾：尚无可用报告。
        try:
            await mgr.read_report(created["run_id"])
        except Exception as exc:  # noqa: BLE001
            missing = (exc.code, exc.status_code)
        try:
            await mgr.read_report("f" * 32)
        except Exception as exc:  # noqa: BLE001
            unknown = (exc.code, exc.status_code)
        store.read_error = StorageError("storage_unavailable", retryable=True)
        try:
            await mgr.read_report(created["run_id"])
        except StorageError as exc:
            unavailable = exc.code
        finally:
            store.read_error = None
            await mgr.close()
        return missing, unknown, unavailable

    missing, unknown, unavailable = run(scenario())
    assert missing == ("report_not_available", 409)
    assert unknown == ("run_not_found", 404)
    assert unavailable == "storage_unavailable"


def test_report_hash_mismatch_is_rejected():
    async def scenario():
        mgr, _runner, store, run_id = await completed_run()
        record = store.reports[(run_id, 1)]
        # 直接改动已提交 payload，模拟数据库侧内容被篡改。
        store.reports[(run_id, 1)] = replace(
            record, public_payload={"markdown": "# tampered"}
        )
        try:
            await mgr.read_report(run_id)
        except Exception as exc:  # noqa: BLE001
            code, status = exc.code, exc.status_code
        else:
            code, status = None, None
        await mgr.close()
        return code, status

    assert run(scenario()) == ("run_persistence_incomplete", 503)


# ---- HTTP 层：报告版本与哈希通过响应头携带 ------------------------------


class StubReadManager:
    """只验证路由与响应头接线，不参与持久化逻辑。"""

    def __init__(self, body, meta):
        self._body = body
        self._meta = meta

    async def read_report(self, run_id):
        return self._body, self._meta

    async def close(self):
        return None


def test_api_report_headers_carry_revision_and_hash():
    meta = {
        "revision": 3,
        "schema_version": 1,
        "snapshot_kind": "answer_first",
        "payload_sha256": "a" * 64,
    }
    stub = StubReadManager({"markdown": "# answer"}, meta)
    with TestClient(create_app(manager_factory=lambda: stub)) as api:
        response = api.get("/api/research/runs/abc/report")
    assert response.status_code == 200
    assert response.json() == {"markdown": "# answer"}
    assert response.headers["X-Report-Revision"] == "3"
    assert response.headers["X-Report-Schema-Version"] == "1"
    assert response.headers["X-Report-Snapshot-Kind"] == "answer_first"
    assert response.headers["X-Report-Payload-SHA256"] == "a" * 64


def test_api_report_without_persistence_omits_version_headers():
    stub = StubReadManager({"markdown": "# answer"}, {})
    with TestClient(create_app(manager_factory=lambda: stub)) as api:
        response = api.get("/api/research/runs/abc/report")
    assert response.status_code == 200
    assert "X-Report-Revision" not in response.headers
