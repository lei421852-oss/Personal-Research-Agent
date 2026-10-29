"""阶段2.5：独立PostgreSQL上的选择写入、重启及冲突回归，不调用Provider。"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.persistence.errors import StorageError
from app.persistence.lifecycle import key_hash
from app.persistence.repository import ConflictError
from app.persistence.serialization import sha256_hex
from app.research.run_manager import MainAgentRunRequest, ResearchRunManager
from tests.integration import test_end_to_end_postgresql as base

pytestmark = base.pytestmark


@pytest.fixture(autouse=True)
def isolated_database():
    target = make_url(base.URL)
    if target.host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    } or not target.database.startswith("personal_agent_test_"):
        pytest.fail(
            "Only the authorized disposable local target is accepted", pytrace=False
        )
    base.cleanup_all()
    yield
    base.cleanup_all()


class SelectionRunner(base.ReportingRunner):
    def __init__(self, selected, *, node="PaperSelection", fail_before=False):
        super().__init__()
        self.selected = selected
        self.node = node
        self.fail_before = fail_before
        self.select_now = None
        self.finish_now = None

    async def run(self, request, sink):
        self.calls += 1
        self.select_now = asyncio.Event()
        self.finish_now = asyncio.Event()
        if self.fail_before:
            raise RuntimeError("selection did not complete")
        await self.select_now.wait()
        sink(
            node_type=self.node,
            node_id="scope",
            status="running" if self.node == "Planner" else "completed",
            event_type="started" if self.node == "Planner" else "completed",
            summary="实际研究范围已确认",
            details={
                "paper_ids"
                if self.node == "Planner"
                else "selected_paper_ids": self.selected
            },
        )
        await self.finish_now.wait()
        return {
            "entry_kind": "react",
            "status": "completed",
            "answer_first": dict(base.REPORT),
        }


def wait_selected(api, identity, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = api.get(f"/api/research/runs/{identity}")
        assert response.status_code == 200
        state = response.json()
        if state["paper_selection"].get("status") == "selected":
            assert state["paper_ids"] == expected
            return state
        time.sleep(0.01)
    raise AssertionError("selection was not committed")


@pytest.mark.parametrize(
    "selected,expected,node",
    [
        (base.PAPERS, base.PAPERS, "PaperSelection"),
        ([" clip-x ", "clip-x"], ["clip-x"], "Planner"),
    ],
)
def test_selection_is_committed_before_finish_and_survives_restart(
    selected, expected, node
):
    runner = SelectionRunner(selected, node=node)
    with TestClient(base.app_for(base.resource(), runner)) as api:
        submitted = base.post(api, key="selection-key")
        assert submitted.status_code == 202
        identity = submitted.json()["run_id"]
        base.wait_status(api, identity, "running")
        before = base.db_rows(identity)["run"]
        assert before["effective_paper_ids"] == []
        assert before["public_state"].get("paper_selection") is None
        api.portal.call(runner.select_now.set)
        wait_selected(api, identity, expected)
        committed = base.db_rows(identity)["run"]
        assert committed["request_payload"]["paper_ids"] == base.PAPERS
        assert committed["effective_paper_ids"] == expected
        assert committed["started_at"] == before["started_at"]
        replay = base.post(api, key="selection-key")
        assert replay.status_code == 200
        assert replay.json()["paper_ids"] == expected
        assert runner.calls == 1
        api.portal.call(runner.finish_now.set)
        base.wait_terminal(api, identity)
        base.assert_single_terminal(base.db_rows(identity))
    rebuilt_runner = base.ReportingRunner()
    with TestClient(base.app_for(base.resource(), rebuilt_runner)) as rebuilt:
        restored = rebuilt.get(f"/api/research/runs/{identity}")
        assert restored.status_code == 200
        assert restored.json()["paper_ids"] == expected
        assert restored.json()["paper_selection"]["selected_paper_ids"] == expected
        replay = base.post(rebuilt, key="selection-key")
        assert replay.status_code == 200
        assert replay.json()["paper_ids"] == expected
        assert replay.json()["run_id"] == identity
        assert rebuilt_runner.calls == 0


def test_failure_before_selection_preserves_undetermined_scope():
    runner = SelectionRunner(None, fail_before=True)
    with TestClient(base.app_for(base.resource(), runner)) as api:
        response = base.post(api, key="before-selection")
        identity = response.json()["run_id"]
        state = base.wait_terminal(api, identity)
        assert state["status"] == "failed"
        row = base.db_rows(identity)["run"]
        assert row["effective_paper_ids"] == []
        assert row["public_state"].get("paper_selection") is None
        assert row["request_payload"]["paper_ids"] == base.PAPERS
        assert state["paper_selection"] == {}


class SelectionFailureRepository:
    def __init__(self, inner):
        self.inner = inner
        self.finalize_calls = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def transition_run(self, *args, **kwargs):
        if "effective_paper_ids" in (kwargs.get("patch") or {}):
            raise StorageError("storage_unavailable", retryable=False)
        return await self.inner.transition_run(*args, **kwargs)

    async def finalize_run(self, *args, **kwargs):
        self.finalize_calls += 1
        return await self.inner.finalize_run(*args, **kwargs)


def test_selection_write_failure_is_not_reported_as_completed():
    res = base.resource()
    repository = SelectionFailureRepository(base.ResearchRepository(res))
    lifecycle, _, _ = base.lifecycle_for(res, repository)
    runner = SelectionRunner(["clip-x"])
    with TestClient(base.app_for(res, runner, lifecycle=lifecycle)) as api:
        response = base.post(api, key="selection-write-failure")
        identity = response.json()["run_id"]
        base.wait_status(api, identity, "running")
        api.portal.call(runner.select_now.set)
        api.portal.call(runner.finish_now.set)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = api.get(f"/api/research/runs/{identity}")
            if state.status_code == 503:
                break
            time.sleep(0.01)
        assert state.status_code == 503
        assert state.json()["error"]["code"] == "run_persistence_incomplete"
        assert state.json()["error"]["retry_mode"] == "reconcile_only"
        row = base.db_rows(identity)
        assert row["run"]["status"] == "running"
        assert row["run"]["effective_paper_ids"] == []
        assert row["run"]["finalize_token"] is None
        assert row["reports"] == []
        assert repository.finalize_calls == 0


def test_restarted_manager_maps_known_fingerprint_conflict_without_second_run():
    async def seed():
        res = base.resource()
        await res.start()
        try:
            await base.ResearchRepository(res).create_run(
                run_id="a" * 32,
                mode="live",
                request_payload=base.PAYLOAD,
                request_hash=sha256_hex(base.PAYLOAD),
                owner_instance_id="b" * 32,
                fingerprint=ResearchRunManager._fingerprint(
                    MainAgentRunRequest(**base.PAYLOAD)
                ),
                idempotency_key_hash=key_hash("original-key"),
            )
        finally:
            await res.close()

    asyncio.run(seed())
    runner = base.ReportingRunner()
    with TestClient(base.app_for(base.resource(), runner)) as api:
        assert not api.app.state.research_manager._runs
        duplicate = base.post(api, key="different-key")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "duplicate_run"
        assert base.count_rows() == 1
        assert runner.calls == 0
        assert not api.app.state.research_manager._runs
        # 同键核对先于指纹检查；未接管旧任务不变成duplicate_run或新调度。
        replay = base.post(api, key="original-key")
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "run_not_attached"
        conflict = base.post(api, key="original-key", question="different")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_key_conflict"
        assert base.count_rows() == 1
        assert runner.calls == 0


def test_current_manager_active_duplicate_stays_409():
    runner = base.BlockingRunner()
    with TestClient(base.app_for(base.resource(), runner)) as api:
        identity = base.post(api, key="running-key").json()["run_id"]
        base.wait_status(api, identity, "running")
        duplicate = base.post(api, key="another-key")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "duplicate_run"
        assert base.count_rows() == 1
        assert runner.calls == 1


@pytest.mark.parametrize(
    "error",
    [
        ConflictError("create_conflict"),
        StorageError("storage_unavailable", retryable=True),
        IntegrityError("fixture", None, RuntimeError("unknown")),
    ],
)
def test_unidentified_create_errors_remain_unknown_and_do_not_start_runner(error):
    class FailingCreateRepository:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        async def create_run(self, **kwargs):
            raise error

    res = base.resource()
    lifecycle, _, _ = base.lifecycle_for(
        res, FailingCreateRepository(base.ResearchRepository(res))
    )
    runner = base.ReportingRunner()
    with TestClient(base.app_for(res, runner, lifecycle=lifecycle)) as api:
        response = base.post(api, key="unknown-key")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "submission_outcome_unknown"
        assert response.json()["error"]["retry_mode"] == "reconcile_only"
        assert runner.calls == 0
        assert base.count_rows() == 0
