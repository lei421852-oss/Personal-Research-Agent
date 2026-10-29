"""阶段2.5：真实选择范围和已知创建冲突的确定性回归。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.persistence.coordinator import PersistenceLatch
from app.persistence.errors import StorageError
from app.persistence.lifecycle import RunPersistence
from app.persistence.repository import (
    AppendResult,
    ConflictError,
    CreateOutcome,
    CreateResult,
    TransitionOutcome,
    TransitionResult,
)
from app.persistence.writer import WriterFailed
from app.research.run_events import RunEvent
from app.research.run_manager import MainAgentRunRequest, RunManagerError
from tests.unit.research.test_manager_admission import (
    PAYLOAD,
    Store,
    record,
)
from tests.unit.research.test_manager_admission import (
    Runner as FakeRunner,
)
from tests.unit.research.test_manager_admission import (
    manager as make_manager,
)

RUN_ID = "a" * 32


def manager(store, runner):
    return make_manager(store, runner=runner)[0]


class SelectionRepository:
    def __init__(self):
        self.row = None
        self.selection_error = None
        self.patches = []
        self.finalized = 0

    async def create_run(self, **values):
        self.row = replace(
            record(run_id=RUN_ID, request_digest=values["request_hash"]),
            request_payload=dict(values["request_payload"]),
            effective_paper_ids=[],
        )
        return CreateResult(CreateOutcome.CREATED, self.row)

    async def get_run(self, run_id):
        return self.row

    async def transition_run(self, run_id, **values):
        patch = values.get("patch", {})
        if "effective_paper_ids" in patch and self.selection_error:
            raise self.selection_error
        assert values["expected_version"] == self.row.state_version
        self.patches.append(dict(patch))
        self.row = replace(
            self.row,
            state_version=self.row.state_version + 1,
            status=values["to_status"],
            **patch,
        )
        return TransitionResult(TransitionOutcome.UPDATED, self.row)

    async def append_events(self, run_id, events, *, expected_last_seq):
        assert expected_last_seq == self.row.last_event_seq
        self.row = replace(self.row, last_event_seq=events[-1].seq)
        return AppendResult(tuple(e.seq for e in events), (), self.row.last_event_seq)

    async def finalize_run(self, command):
        self.finalized += 1


def selection_event(*, seq=1, selected=None, node="PaperSelection", status="completed"):
    return RunEvent(
        run_id=RUN_ID,
        event_id=seq,
        node_type=node,
        node_id="scope",
        status=status,
        event_type="started" if node == "Planner" else status,
        summary="范围已经由运行时确认",
        details={"paper_ids" if node == "Planner" else "selected_paper_ids": selected},
        source="live",
    )


@pytest.mark.parametrize(
    "selected,expected",
    [
        (["clip-x", "blip-y"], ["clip-x", "blip-y"]),
        ([" clip-x ", "clip-x"], ["clip-x"]),
    ],
)
def test_selected_scope_is_committed_without_overwriting_request(selected, expected):
    async def run():
        repository = SelectionRepository()
        lifecycle = RunPersistence(repository, owner_instance_id="b" * 32)
        await lifecycle.start(run_id=RUN_ID, payload=PAYLOAD, fingerprint=None)
        try:
            assert repository.row.effective_paper_ids == []
            lifecycle.on_event(RUN_ID, selection_event(selected=selected))
            await lifecycle._writers[RUN_ID].flush()
            assert repository.row.effective_paper_ids == expected
            assert repository.row.request_payload == PAYLOAD
            assert repository.row.public_state["paper_selection"] == {
                "status": "selected",
                "selected_paper_ids": expected,
            }
        finally:
            await lifecycle.close()

    asyncio.run(run())


def test_explicit_planner_scope_and_multiple_handoffs_keep_confirmed_union():
    async def run():
        repository = SelectionRepository()
        lifecycle = RunPersistence(repository, owner_instance_id="b" * 32)
        await lifecycle.start(run_id=RUN_ID, payload=PAYLOAD, fingerprint=None)
        try:
            for seq, selected in enumerate((["clip-x"], ["blip-y", "clip-x"]), 1):
                lifecycle.on_event(
                    RUN_ID,
                    selection_event(
                        seq=seq, selected=selected, node="Planner", status="running"
                    ),
                )
            await lifecycle._writers[RUN_ID].flush()
            assert repository.row.effective_paper_ids == ["clip-x", "blip-y"]
            assert repository.row.request_payload == PAYLOAD
        finally:
            await lifecycle.close()

    asyncio.run(run())


@pytest.mark.parametrize("selected", [None, [], [""], ["clip-x", None]])
def test_unconfirmed_or_invalid_selection_never_copies_request_scope(selected):
    async def run():
        repository = SelectionRepository()
        lifecycle = RunPersistence(repository, owner_instance_id="b" * 32)
        await lifecycle.start(run_id=RUN_ID, payload=PAYLOAD, fingerprint=None)
        try:
            lifecycle.on_event(RUN_ID, selection_event(selected=selected))
            await lifecycle._writers[RUN_ID].flush()
            assert repository.row.effective_paper_ids == []
            assert "paper_selection" not in repository.row.public_state
        finally:
            await lifecycle.close()

    asyncio.run(run())


def test_selection_write_failure_cannot_finalize_success():
    async def run():
        repository = SelectionRepository()
        repository.selection_error = StorageError(
            "storage_unavailable", retryable=False
        )
        latch = PersistenceLatch()
        lifecycle = RunPersistence(repository, owner_instance_id="b" * 32, latch=latch)
        await lifecycle.start(run_id=RUN_ID, payload=PAYLOAD, fingerprint=None)
        try:
            lifecycle.on_event(RUN_ID, selection_event(selected=["clip-x"]))
            with pytest.raises(WriterFailed):
                await lifecycle.finish(RUN_ID, {"status": "completed"})
            assert repository.row.effective_paper_ids == []
            assert repository.finalized == 0
            assert latch.tripped
        finally:
            await lifecycle.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error,code,status",
    [
        (
            ConflictError(
                "duplicate_active_fingerprint",
                detail="uq_research_runs_active_fingerprint",
            ),
            "duplicate_run",
            409,
        ),
        (
            ConflictError("duplicate_active_fingerprint", detail="other_constraint"),
            "submission_outcome_unknown",
            503,
        ),
        (
            ConflictError(
                "create_conflict", detail="uq_research_runs_active_fingerprint"
            ),
            "submission_outcome_unknown",
            503,
        ),
        (ConflictError("version_conflict"), "submission_outcome_unknown", 503),
        (
            IntegrityError("fixture", None, RuntimeError("unidentified")),
            "submission_outcome_unknown",
            503,
        ),
        (
            StorageError("storage_unavailable", retryable=True),
            "submission_outcome_unknown",
            503,
        ),
    ],
)
def test_only_identified_active_fingerprint_conflict_maps_to_409(error, code, status):
    async def run():
        store = Store()
        store.fail_start = error
        runner = FakeRunner()
        runtime = manager(store, runner)
        try:
            with pytest.raises(RunManagerError) as caught:
                await runtime.create(
                    MainAgentRunRequest(**PAYLOAD), idempotency_key="scope-key"
                )
            assert caught.value.code == code
            assert caught.value.status_code == status
            assert runner.calls == 0
            assert not store.by_run
            assert not runtime._runs
            if status == 503:
                assert caught.value.retry_mode == "reconcile_only"
            else:
                assert len(runtime._admissions) == 0
        finally:
            await runtime.close()

    asyncio.run(run())


def test_persisted_selection_overrides_uncommitted_memory_for_get_and_replay():
    runner = FakeRunner()
    runtime = manager(Store(), runner)
    row = replace(
        record(run_id=RUN_ID, request_digest="hash"),
        effective_paper_ids=["clip-x"],
        public_state={
            "paper_selection": {"status": "selected", "selected_paper_ids": ["clip-x"]}
        },
    )
    memory = SimpleNamespace(paper_ids=["blip-y"], report=None, execution=None)
    assert runtime._record_status(row, memory)["paper_ids"] == ["clip-x"]
    assert runtime._persisted_status(row, replayed=True)["paper_ids"] == ["clip-x"]
    unknown = replace(row, effective_paper_ids=[], public_state={})
    assert runtime._persisted_status(unknown)["paper_ids"] == PAYLOAD["paper_ids"]
    assert runtime._persisted_status(unknown)["paper_selection"] == {}


def test_known_write_failure_never_falls_back_to_memory_success():
    async def run():
        runtime = manager(Store(), FakeRunner())
        runtime._runs[RUN_ID] = SimpleNamespace(persistence_error="writer_failed")
        with pytest.raises(RunManagerError) as caught:
            await runtime.read_status(RUN_ID)
        assert caught.value.code == "run_persistence_incomplete"
        assert caught.value.status_code == 503
        assert caught.value.retry_mode == "reconcile_only"

    asyncio.run(run())


def test_repository_unknown_constraint_never_reports_created():
    from contextlib import asynccontextmanager

    from app.persistence.repository import ResearchRepository

    class Session:
        def add(self, row):
            pass

        @asynccontextmanager
        async def begin(self):
            yield

        async def flush(self):
            raise IntegrityError("fixture", None, RuntimeError("constraint unknown"))

    class Resource:
        @asynccontextmanager
        async def session(self):
            yield Session()

    async def run():
        with pytest.raises(ConflictError, match="create_conflict"):
            await ResearchRepository(Resource()).create_run(
                run_id=RUN_ID,
                mode="live",
                request_payload=PAYLOAD,
                request_hash="b" * 64,
                owner_instance_id="c" * 32,
            )

    asyncio.run(run())


def test_disabled_mode_keeps_existing_memory_selection_behavior():
    from app.research.run_manager import ResearchRunManager
    from tests.unit.research.test_manager_admission import Deps, Replays

    class SelectingRunner(FakeRunner):
        async def run(self, request, sink):
            self.calls += 1
            sink(
                node_type="PaperSelection",
                node_id="selection",
                status="completed",
                event_type="completed",
                summary="真实选择已完成",
                details={"selected_paper_ids": ["clip-x"]},
            )
            return {"status": "completed", "research": {"status": "completed"}}

    async def run():
        runner = SelectingRunner()
        runtime = ResearchRunManager(runner, Deps(), Replays())
        try:
            submitted = await runtime.create(MainAgentRunRequest(**PAYLOAD))
            await runtime._runs[submitted["run_id"]].task
            state = await runtime.read_status(submitted["run_id"])
            assert state["paper_ids"] == ["clip-x"]
            assert "persisted" not in state
            assert runtime.lifecycle is None
        finally:
            await runtime.close()

    asyncio.run(run())
