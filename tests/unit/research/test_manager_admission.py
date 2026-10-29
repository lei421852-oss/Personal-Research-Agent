"""R4 admission 临界区与错误码矩阵：确定性屏障，不用 sleep 猜状态。

覆盖交接说明 §7.3 的 1–8 项；第 9 项（唯一冲突后新事务读胜者）由真实 PostgreSQL 用例承担。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.lifecycle import request_hash
from app.persistence.repository import RunRecord
from app.research.run_manager import (
    MainAgentRunRequest,
    ResearchRunManager,
    RunManagerError,
)
from app.research.scheduling import AdmissionState
from app.workbench import create_app
from tests.unit.persistence.fakes import Factories

PAPERS = ["clip-x", "blip-y"]
PAYLOAD = {
    "question": "q",
    "paper_ids": PAPERS,
    "language_mode": "auto",
    "max_model_calls": 200,
}
URL = (
    "postgresql+asyncpg://reader:fixture-password@localhost/"
    "personal_agent_test_unit?ssl=disable"
)


class Deps:
    def papers(self):
        return [{"paper_id": paper_id} for paper_id in PAPERS]

    def health(self):
        return {"real_research_ready": True, "language_adapter_ready": True}


class Replays:
    def list_cases(self):
        return []


class Runner:
    main_agent_entry = False

    def __init__(self):
        self.calls = 0

    async def run(self, request, sink):
        self.calls += 1
        return {"status": "completed", "research": {"status": "completed"}}


class Started:
    def __init__(self, record, replayed):
        self.record = record
        self.replayed = replayed


def record(*, run_id, request_digest, status="queued"):
    now = datetime.now(UTC)
    terminal = status in {"completed", "failed", "cancelled"}
    return RunRecord(
        run_id=run_id,
        mode="live",
        status=status,
        state_version=2 if terminal else 0,
        last_event_seq=0,
        current_report_revision=None,
        created_at=now,
        started_at=now if terminal else None,
        finished_at=now if terminal else None,
        error=None,
        metrics={},
        cancel_requested=False,
        event_limit_reached=False,
        public_state={},
        effective_paper_ids=list(PAPERS),
        finalize_token=None,
        finalize_sha256=None,
        request_hash=request_digest,
        request_payload=dict(PAYLOAD),
    )


class Store:
    """最小持久化替身：同键原子裁决 + 「已提交、未注册」可控屏障。"""

    def __init__(self):
        self.by_key: dict[str, RunRecord] = {}
        self.by_run: dict[str, RunRecord] = {}
        self.hold: asyncio.Event | None = None
        self.fail_start: Exception | None = None
        self.fail_after_commit: Exception | None = None
        self.fail_finish: Exception | None = None
        self.finished: list[tuple[str, dict]] = []
        self.committed = asyncio.Event()
        self.find_count = 0
        self.find_target: int | None = None
        self.find_event = asyncio.Event()

    def watch_next_find(self) -> None:
        """把「重放方已进入归属判定」变成一个可等待的信号点。"""

        self.find_target = self.find_count + 1
        self.find_event.clear()

    async def start(
        self,
        *,
        run_id,
        payload,
        fingerprint,
        created_at=None,
        idempotency_key_hash=None,
    ):
        if self.fail_start is not None:
            raise self.fail_start
        if idempotency_key_hash is not None and idempotency_key_hash in self.by_key:
            # 唯一约束裁决：本实例不是胜方。
            return Started(self.by_key[idempotency_key_hash], True)
        row = record(run_id=run_id, request_digest=request_hash(payload))
        if idempotency_key_hash is not None:
            self.by_key[idempotency_key_hash] = row
        self.by_run[run_id] = row
        # 从这里起记录对其他请求可见；到返回之间允许协程切换。
        self.committed.set()
        if self.fail_after_commit is not None:
            # 已写入但无法确认提交结果：模拟 COMMIT 已发出后连接中断。
            raise self.fail_after_commit
        if self.hold is not None:
            await self.hold.wait()
        return Started(row, False)

    async def find_by_key(self, digest):
        self.find_count += 1
        if self.find_target is not None and self.find_count >= self.find_target:
            self.find_event.set()
        return self.by_key.get(digest)

    async def mark_running(self, run_id):
        row = self.by_run.get(run_id)
        if row is not None and row.status == "queued":
            self.by_run[run_id] = replace(row, status="running")

    def on_event(self, run_id, event):
        return None

    async def finish(self, run_id, snapshot):
        if self.fail_finish is not None:
            raise self.fail_finish
        status = snapshot.get("status", "failed")
        row = self.by_run.get(run_id)
        if row is not None:
            updated = replace(
                row,
                status=status,
                finished_at=datetime.now(UTC),
                error=snapshot.get("error"),
            )
            self.by_run[run_id] = updated
            for key, value in list(self.by_key.items()):
                if value.run_id == run_id:
                    self.by_key[key] = updated
        self.finished.append((run_id, snapshot))

    async def close(self):
        return None


def failing_scheduler(pending, name=None):
    """模拟 create_task 失败；协程交给 `_schedule` 关闭，这里不吞掉它。"""

    del pending, name
    raise RuntimeError("scheduler unavailable")


def manager(store, *, runner=None, **kwargs):
    runner = runner if runner is not None else Runner()
    return (
        ResearchRunManager(runner, Deps(), Replays(), lifecycle=store, **kwargs),
        runner,
    )


def request(question="q"):
    return MainAgentRunRequest(question=question, paper_ids=PAPERS)


def body(question="q"):
    return {"question": question, "paper_ids": PAPERS}


def run(coro):
    return asyncio.run(coro)


async def settle(mgr, run_id):
    """先等研究任务结束，再等 done callback 登记并完成收尾任务。"""

    state = mgr._runs[run_id]
    if state.task is not None:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(state.task), 5)
    for _ in range(20):
        await asyncio.sleep(0)
        if state.finalize_task is not None:
            break
    if state.finalize_task is not None:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(state.finalize_task), 5)


async def caught(coro):
    try:
        return await coro, None
    except RunManagerError as exc:  # 只接受稳定公开错误
        return None, exc


# 1. 新建成功：登记为 registered，且只有一个 Task、Runner 只被调用一次。
def test_new_run_registers_exactly_one_task():
    async def scenario():
        store = Store()
        mgr, runner = manager(store)
        created = await mgr.create(request(), idempotency_key="k1")
        row = mgr._runs[created["run_id"]]
        task_registered = row.task is not None
        operation = mgr._admissions.peek(created["run_id"])
        await settle(mgr, created["run_id"])
        state = operation.state
        await mgr.close()
        return created, task_registered, state, runner.calls, len(mgr._runs)

    created, task_registered, state, calls, retained = run(scenario())
    assert "replayed" not in created
    assert task_registered is True
    assert state is AdmissionState.REGISTERED
    assert calls == 1
    assert retained == 1


# 2. 同键同请求：返回原 run，不建第二个 Task，Runner 调用数不变。
def test_same_key_same_request_replays_original_run():
    async def scenario():
        store = Store()
        mgr, runner = manager(store)
        first = await mgr.create(request(), idempotency_key="k1")
        await settle(mgr, first["run_id"])
        second = await mgr.create(request(), idempotency_key="k1")
        await mgr.close()
        return first, second, runner.calls, len(store.by_run)

    first, second, calls, rows = run(scenario())
    assert second["replayed"] is True
    assert second["run_id"] == first["run_id"]
    assert calls == 1
    assert rows == 1


# 3. 同键不同请求：冲突，且不产生任何新记录。
def test_same_key_different_request_conflicts_without_new_record():
    async def scenario():
        store = Store()
        mgr, _ = manager(store)
        await mgr.create(request("q1"), idempotency_key="k1")
        _, error = await caught(mgr.create(request("q2"), idempotency_key="k1"))
        await mgr.close()
        return error, len(store.by_run)

    error, rows = run(scenario())
    assert error.code == "idempotency_key_conflict"
    assert error.status_code == 409
    assert rows == 1


# 4. 只查重放：命中返回原 run，未命中 404，两种情况都不建记录、不建 Task。
def test_replay_only_never_creates_a_run():
    async def scenario():
        store = Store()
        mgr, runner = manager(store)
        first = await mgr.create(request(), idempotency_key="k1")
        await settle(mgr, first["run_id"])
        hit = await mgr.create(request(), idempotency_key="k1", replay_only=True)
        _, miss = await caught(
            mgr.create(request(), idempotency_key="k2", replay_only=True)
        )
        await mgr.close()
        return first, hit, miss, runner.calls, len(store.by_run)

    first, hit, miss, calls, rows = run(scenario())
    assert hit["replayed"] is True
    assert hit["run_id"] == first["run_id"]
    assert miss.code == "submission_not_found"
    assert miss.status_code == 404
    assert calls == 1
    assert rows == 1


# 5. 并发同键（两个实例共享存储）：数据库唯一约束裁决，只有一个胜方。
def test_concurrent_same_key_has_one_winner():
    async def scenario():
        store = Store()
        store.hold = asyncio.Event()
        winner_mgr, winner_runner = manager(store)
        leader = asyncio.create_task(winner_mgr.create(request(), idempotency_key="k1"))
        await store.committed.wait()
        loser_mgr, loser_runner = manager(store)
        _, loser_error = await caught(loser_mgr.create(request(), idempotency_key="k1"))
        loser_tasks = sum(1 for r in loser_mgr._runs.values() if r.task is not None)
        store.hold.set()
        created = await asyncio.wait_for(leader, 5)
        winner_tasks = sum(1 for r in winner_mgr._runs.values() if r.task is not None)
        await settle(winner_mgr, created["run_id"])
        await loser_mgr.close()
        await winner_mgr.close()
        return (
            created,
            loser_error,
            winner_tasks,
            loser_tasks,
            winner_runner.calls,
            loser_runner.calls,
            len(store.by_run),
        )

    (
        _created,
        loser_error,
        winner_tasks,
        loser_tasks,
        winner_calls,
        loser_calls,
        rows,
    ) = run(scenario())
    assert winner_tasks == 1
    assert loser_tasks == 0
    assert loser_calls == 0
    assert winner_calls == 1
    assert rows == 1
    # 败方拿不到归属：只能是「不能重放」或（若已终态）重放，绝不新建。
    assert loser_error is None or loser_error.code == "run_not_attached"


# 6. 提交后、注册前的受控屏障：重放方必须等待，不能提前 409 或建第二个 Task。
def test_replay_waits_across_commit_before_registration():
    async def scenario():
        store = Store()
        store.hold = asyncio.Event()
        mgr, runner = manager(store)
        leader = asyncio.create_task(mgr.create(request(), idempotency_key="k1"))
        await store.committed.wait()
        store.watch_next_find()
        follower = asyncio.create_task(mgr.create(request(), idempotency_key="k1"))
        await asyncio.wait_for(store.find_event.wait(), 5)
        waited = not follower.done()
        tasks_while_waiting = sum(1 for r in mgr._runs.values() if r.task is not None)
        calls_while_waiting = runner.calls
        store.hold.set()
        replayed = await asyncio.wait_for(follower, 5)
        await asyncio.wait_for(leader, 5)
        await settle(mgr, replayed["run_id"])
        await mgr.close()
        return (
            replayed,
            waited,
            tasks_while_waiting,
            calls_while_waiting,
            runner.calls,
            len(store.by_run),
        )

    (
        replayed,
        waited,
        tasks_while_waiting,
        calls_while_waiting,
        calls,
        rows,
    ) = run(scenario())
    assert waited is True
    assert tasks_while_waiting == 0
    assert calls_while_waiting == 0
    assert replayed["replayed"] is True
    assert calls == 1
    assert rows == 1


# 7. 调度结果未知：等待超时必须报 outcome_unknown，且不得改写数据库状态。
def test_unsettled_admission_reports_outcome_unknown():
    async def scenario():
        store = Store()
        store.hold = asyncio.Event()
        mgr, _ = manager(store, admission_timeout=0.05)
        leader = asyncio.create_task(mgr.create(request(), idempotency_key="k1"))
        await store.committed.wait()
        store.watch_next_find()
        follower = asyncio.create_task(mgr.create(request(), idempotency_key="k1"))
        await asyncio.wait_for(store.find_event.wait(), 5)
        _, error = await caught(asyncio.wait_for(follower, 5))
        row_status = next(iter(store.by_run.values())).status
        tasks = sum(1 for r in mgr._runs.values() if r.task is not None)
        store.hold.set()
        created = await asyncio.wait_for(leader, 5)
        await settle(mgr, created["run_id"])
        await mgr.close()
        return error, row_status, tasks

    error, row_status, tasks = run(scenario())
    assert error.code == "task_scheduling_outcome_unknown"
    assert error.status_code == 503
    assert error.retry_mode == "reconcile_only"
    # 未知 ≠ 未安排：库里仍是非终态，也没有出现第二个 Task。
    assert row_status in {"queued", "running"}
    assert tasks == 0


# 8a. 已确认未安排 + 失败收尾已提交：500 task_scheduling_failed。
def test_confirmed_scheduling_failure_closes_out_as_failed():
    async def scenario():
        store = Store()
        mgr, runner = manager(store, scheduler=failing_scheduler)
        _, error = await caught(mgr.create(request(), idempotency_key="k1"))
        run_id = next(iter(store.by_run))
        row = store.by_run[run_id]
        task = mgr._runs[run_id].task
        persisted = mgr._runs[run_id].persisted
        await mgr.close()
        return error, run_id, row, task, persisted, runner.calls

    error, run_id, row, task, persisted, calls = run(scenario())
    assert error.code == "task_scheduling_failed"
    assert error.status_code == 500
    assert error.retry_mode == "safe_retry"
    assert error.run_id == run_id
    assert row.status == "failed"
    assert row.error["code"] == "task_scheduling_failed"
    assert task is None
    assert persisted is True
    assert calls == 0


# 8b. 已确认未安排 + 失败收尾未提交：503 run_persistence_incomplete。
def test_uncommitted_close_out_reports_incomplete_persistence():
    async def scenario():
        store = Store()
        store.fail_finish = RuntimeError("storage down")
        mgr, runner = manager(store, scheduler=failing_scheduler)
        _, error = await caught(mgr.create(request(), idempotency_key="k1"))
        run_id = next(iter(store.by_run))
        persisted = mgr._runs[run_id].persisted
        await mgr.close()
        return error, run_id, persisted, runner.calls

    error, run_id, persisted, calls = run(scenario())
    assert error.code == "run_persistence_incomplete"
    assert error.status_code == 503
    assert error.retry_mode == "reconcile_only"
    assert error.run_id == run_id
    assert persisted is False
    assert calls == 0


# 9. 提交结果未知：记录可能已落库但无法确认；同键重放方必须拿到同一判定。
def test_submission_unknown_is_reported_to_replayers():
    async def scenario():
        store = Store()
        store.fail_after_commit = RuntimeError("commit ack lost")
        mgr, runner = manager(store)
        _, first = await caught(mgr.create(request(), idempotency_key="k1"))
        _, second = await caught(mgr.create(request(), idempotency_key="k1"))
        await mgr.close()
        return first, second, runner.calls, len(store.by_run), len(mgr._runs)

    first, second, calls, rows, retained = run(scenario())
    assert first.code == "submission_outcome_unknown"
    assert first.status_code == 503
    assert first.retry_mode == "reconcile_only"
    assert second.code == first.code
    assert second.run_id == first.run_id
    assert calls == 0
    assert rows == 1
    assert retained == 0


# 错误码矩阵到 HTTP 层的对齐（202 / 200 / 409 / 404 / 500 / 503）。
def client(mgr):
    factories = Factories()
    database = DatabaseResource(
        DatabaseSettings(enabled=True, url=URL),
        engine_factory=factories.create_engine,
        session_factory_builder=factories.make_sessions,
    )
    return TestClient(
        create_app(
            manager_factory=lambda: mgr,
            database_settings=database.settings,
            database_factory=lambda _: database,
        )
    )


def test_api_new_run_is_202_and_replay_returns_200():
    store = Store()
    mgr, runner = manager(store)
    with client(mgr) as api:
        first = api.post(
            "/api/research/runs", json=body(), headers={"Idempotency-Key": "k1"}
        )
        second = api.post(
            "/api/research/runs", json=body(), headers={"Idempotency-Key": "k1"}
        )
    assert first.status_code == 202
    assert "Idempotency-Replayed" not in first.headers
    assert second.status_code == 200
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json()["run_id"] == first.json()["run_id"]
    assert len(store.by_run) == 1
    assert runner.calls <= 1


def test_api_conflict_and_missing_record_codes():
    store = Store()
    mgr, _ = manager(store)
    with client(mgr) as api:
        api.post(
            "/api/research/runs", json=body("q1"), headers={"Idempotency-Key": "k1"}
        )
        conflict = api.post(
            "/api/research/runs", json=body("q2"), headers={"Idempotency-Key": "k1"}
        )
        missing = api.post(
            "/api/research/runs",
            json=body(),
            headers={"Idempotency-Key": "k9", "Idempotency-Replay-Only": "true"},
        )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "submission_not_found"


def test_api_scheduling_failure_codes_reach_callers():
    failed_store = Store()
    failed_mgr, _ = manager(failed_store, scheduler=failing_scheduler)
    with client(failed_mgr) as api:
        failed = api.post(
            "/api/research/runs", json=body(), headers={"Idempotency-Key": "k1"}
        )

    incomplete_store = Store()
    incomplete_store.fail_finish = RuntimeError("storage down")
    incomplete_mgr, _ = manager(incomplete_store, scheduler=failing_scheduler)
    with client(incomplete_mgr) as api:
        incomplete = api.post(
            "/api/research/runs", json=body(), headers={"Idempotency-Key": "k1"}
        )

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "task_scheduling_failed"
    assert failed.json()["error"]["retry_mode"] == "safe_retry"
    assert incomplete.status_code == 503
    assert incomplete.json()["error"]["code"] == "run_persistence_incomplete"
    assert incomplete.json()["error"]["retry_mode"] == "reconcile_only"
