"""离线穷举 Repository 的判定分支：九个终态分支、五个核对分支与事件计划。

不连接数据库；I/O 层用最小的假资源模拟存储失败。
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from app.persistence.errors import StorageError
from app.persistence.repository import (
    ConflictError,
    CreateOutcome,
    EventCommand,
    FinalizeAttempt,
    FinalizeOutcome,
    ReconcileOutcome,
    RepositoryError,
    ResearchRepository,
    RunState,
    canonical_request,
    decide_create_replay,
    decide_finalize,
    decide_reconcile,
    event_hash,
    plan_events,
)
from app.persistence.serialization import build_finalize_command, build_report_command

RUN_ID = "a" * 32
TOKEN = "b" * 32
REPORT_TOKEN = "c" * 32
FINISHED = datetime(2026, 10, 25, 0, 31, 12, 184203, tzinfo=UTC)


class RaisingResource:
    """一进入 session 就失败，用于验证未知/不可用分支。"""

    def __init__(self, code="storage_unavailable"):
        self.code = code

    def session(self):
        raise StorageError(self.code, retryable=True)


def report(expected=None):
    return build_report_command(
        expected_current_revision=expected,
        revision_token=REPORT_TOKEN,
        schema_version=1,
        snapshot_kind="answer_first",
        public_payload={"answer_status": "answered"},
    )


def finalize(**overrides):
    arguments = {
        "run_id": RUN_ID,
        "finalize_token": TOKEN,
        "expected_state_version": 3,
        "status": "completed",
        "finished_at": FINISHED,
        "error": None,
        "metrics": {"model_calls": 3},
        "expected_last_event_seq": 5,
        "final_last_event_seq": 5,
        "expected_current_revision": None,
        "report": report(),
    }
    arguments.update(overrides)
    return build_finalize_command(**arguments)


def state(**overrides):
    values = {
        "run_id": RUN_ID,
        "status": "running",
        "state_version": 3,
        "last_event_seq": 5,
        "current_report_revision": None,
    }
    values.update(overrides)
    return RunState(**values)


# ---------------------------------------------------------------- 终态九分支


def test_finalize_not_found():
    assert decide_finalize(None, finalize()) is FinalizeOutcome.NOT_FOUND


def test_finalize_new_when_everything_matches():
    assert decide_finalize(state(), finalize()) is FinalizeOutcome.NEW


def test_finalize_new_without_report():
    command = finalize(report=None)
    assert decide_finalize(state(), command) is FinalizeOutcome.NEW


def test_finalize_already_committed():
    committed = state(
        status="completed",
        finalize_token=TOKEN,
        finalize_sha256=finalize().finalize_sha256,
    )
    assert decide_finalize(committed, finalize()) is FinalizeOutcome.ALREADY_COMMITTED


def test_finalize_content_conflict_on_same_token_different_hash():
    committed = state(status="failed", finalize_token=TOKEN, finalize_sha256="0" * 64)
    assert decide_finalize(committed, finalize()) is FinalizeOutcome.CONTENT_CONFLICT


def test_finalize_foreign_terminal():
    committed = state(
        status="cancelled", finalize_token="d" * 32, finalize_sha256="0" * 64
    )
    assert decide_finalize(committed, finalize()) is FinalizeOutcome.FOREIGN_TERMINAL


def test_finalize_version_conflict_precedes_event_gap():
    stale = state(state_version=2, last_event_seq=4, current_report_revision=1)
    assert decide_finalize(stale, finalize()) is FinalizeOutcome.VERSION_CONFLICT


def test_finalize_event_gap_conflict():
    assert (
        decide_finalize(state(last_event_seq=4), finalize())
        is FinalizeOutcome.EVENT_GAP_CONFLICT
    )


def test_finalize_report_pointer_conflict_when_pointer_moved():
    assert (
        decide_finalize(state(current_report_revision=1), finalize())
        is FinalizeOutcome.REPORT_POINTER_CONFLICT
    )


def test_finalize_report_pointer_conflict_when_revision_mismatch():
    command = finalize(expected_current_revision=1, report=report(expected=1))
    stale = state(current_report_revision=2)
    assert decide_finalize(stale, command) is FinalizeOutcome.REPORT_POINTER_CONFLICT


def test_finalize_unknown_on_storage_failure():
    repository = ResearchRepository(RaisingResource())
    result = asyncio.run(repository.finalize_run(finalize()))
    assert result.outcome is FinalizeOutcome.UNKNOWN
    assert result.run is None


# ---------------------------------------------------------------- 核对五分支


def test_reconcile_still_pending_when_active():
    assert decide_reconcile(state(), token=TOKEN, sha256="0" * 64) is (
        ReconcileOutcome.STILL_PENDING
    )


def test_reconcile_still_pending_when_missing():
    assert decide_reconcile(None, token=TOKEN, sha256="0" * 64) is (
        ReconcileOutcome.STILL_PENDING
    )


def test_reconcile_confirmed():
    digest = finalize().finalize_sha256
    committed = state(status="completed", finalize_token=TOKEN, finalize_sha256=digest)
    assert (
        decide_reconcile(committed, token=TOKEN, sha256=digest)
        is ReconcileOutcome.CONFIRMED
    )


def test_reconcile_content_conflict():
    committed = state(
        status="completed", finalize_token=TOKEN, finalize_sha256="1" * 64
    )
    assert (
        decide_reconcile(committed, token=TOKEN, sha256="2" * 64)
        is ReconcileOutcome.CONTENT_CONFLICT
    )


def test_reconcile_foreign_terminal():
    committed = state(
        status="cancelled", finalize_token="e" * 32, finalize_sha256="1" * 64
    )
    assert (
        decide_reconcile(committed, token=TOKEN, sha256="2" * 64)
        is ReconcileOutcome.FOREIGN_TERMINAL
    )


def test_reconcile_unavailable():
    repository = ResearchRepository(RaisingResource())
    result = asyncio.run(
        repository.reconcile_finalize(RUN_ID, token=TOKEN, sha256="0" * 64)
    )
    assert result.outcome is ReconcileOutcome.UNAVAILABLE


# ---------------------------------------------------------------- 终态状态机


def test_attempt_allows_retry_only_after_rollback():
    attempt = FinalizeAttempt()
    assert attempt.can_finalize
    attempt.start(TOKEN)
    assert not attempt.can_finalize
    attempt.mark_rolled_back()
    assert attempt.can_finalize
    attempt.start(TOKEN)
    attempt.mark_committed()
    assert not attempt.can_finalize
    with pytest.raises(RepositoryError):
        attempt.start(TOKEN)


def test_attempt_unknown_requires_reconcile_and_blocks_writes():
    attempt = FinalizeAttempt()
    attempt.start(TOKEN)
    attempt.mark_unknown()
    assert attempt.must_reconcile
    assert not attempt.can_finalize
    with pytest.raises(RepositoryError):
        attempt.start(TOKEN)


def test_attempt_rejects_invalid_transitions():
    attempt = FinalizeAttempt()
    with pytest.raises(RepositoryError):
        attempt.mark_committed()
    attempt.start(TOKEN)
    with pytest.raises(RepositoryError):
        attempt.mark_unknown()
        attempt.mark_unknown()


# ---------------------------------------------------------------- 事件计划


def event(seq=1, summary="主Agent已结束"):
    return EventCommand.build(
        seq=seq,
        node_type="MainAgent",
        node_id="aggregate:main_agent",
        status="completed",
        event_type="completed",
        source="live",
        summary=summary,
        details={"answer_status": "answered"},
    )


def test_event_hash_is_deterministic_and_content_sensitive():
    assert event_hash(event()) == event_hash(event())
    assert event_hash(event()) != event_hash(event(summary="不同的摘要"))
    assert len(event_hash(event())) == 64


def test_plan_events_separates_inserts_and_duplicates():
    first = event(1)
    existing = {1: first.payload_sha256}
    to_insert, duplicates = plan_events(existing, [first, event(2)])
    assert [item.seq for item in to_insert] == [2]
    assert duplicates == [1]


def test_plan_events_rejects_same_seq_different_content():
    existing = {1: event(1).payload_sha256}
    with pytest.raises(ConflictError):
        plan_events(existing, [event(1, summary="被改写")])


def test_event_command_omits_occurred_at_for_derived_sources():
    live = event()
    derived = EventCommand.build(
        seq=1,
        node_type="Planner",
        node_id="planner",
        status="skipped",
        event_type="skipped",
        source="derived",
        summary="派生状态",
    )
    assert live.content()["occurred_at"] is None
    assert derived.content()["occurred_at"] is None


# ---------------------------------------------------------------- 创建幂等


def test_create_replay_decision():
    assert decide_create_replay(None, "h1") is CreateOutcome.CREATED
    assert decide_create_replay("h1", "h1") is CreateOutcome.REPLAYED
    with pytest.raises(ConflictError):
        decide_create_replay("h1", "h2")


def test_request_hash_is_order_insensitive():
    first = canonical_request({"question": "q", "paper_ids": ["b", "a"]})
    second = canonical_request({"paper_ids": ["b", "a"], "question": "q"})
    assert first == second
    assert canonical_request({"question": "q2", "paper_ids": ["b", "a"]}) != first


# ---------------------------------------------------------------- 冻结与错误


def test_run_state_is_frozen():
    value = state()
    with pytest.raises(FrozenInstanceError):
        value.status = "completed"


def test_conflict_error_carries_code_and_run_id():
    error = ConflictError("report_pointer_conflict", run_id=RUN_ID)
    assert error.code == "report_pointer_conflict"
    assert error.run_id == RUN_ID


def test_finalize_command_hash_is_stable_across_rebuild():
    assert finalize().finalize_sha256 == finalize().finalize_sha256
    assert finalize(status="failed").finalize_sha256 != finalize().finalize_sha256


def test_merged_state_flags():
    assert state(status="queued").terminal is False
    assert state(status="completed").terminal is True
