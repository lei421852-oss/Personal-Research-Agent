"""只验证任务管理行为；模拟执行不作为研究质量证据。"""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.research.run_manager import (
    ResearchRunManager,
    ResearchRunRequest,
    RunManagerError,
)
from app.research.run_replay import ReplayRepository

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ["blip2-2301.12597v1", "flamingo-2204.14198v1"]


class FakeDependencies:
    def __init__(self, ready=True):
        self.ready = ready

    def papers(self):
        return [{"paper_id": identity, "title": identity} for identity in PAPERS]

    def health(self):
        return {
            "ui_ready": True,
            "replay_ready": True,
            "real_research_ready": self.ready,
        }


class FakeRunner:
    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = []
        self.active = 0
        self.peak = 0

    async def run(self, request, emit):
        self.calls.append(request.question)
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        emit(
            node_type="Planner",
            node_id="planner",
            status="running",
            event_type="started",
            summary="模拟执行用于任务管理测试。",
            details={},
        )
        try:
            await self.release.wait()
            return {"status": "completed"}
        finally:
            self.active -= 1


def request(question="compare architecture"):
    return ResearchRunRequest(question=question, paper_ids=PAPERS)


def make_manager(runner=None, dependencies=None, **limits):
    return ResearchRunManager(
        runner or FakeRunner(),
        dependencies or FakeDependencies(),
        ReplayRepository(ROOT),
        **limits,
    )


def test_background_state_and_event_cursor():
    async def check():
        runner = FakeRunner()
        manager = make_manager(runner)
        created = await manager.create(request())
        assert created["status"] == "queued"
        await runner.started.wait()
        await asyncio.sleep(0)
        assert manager.status(created["run_id"])["status"] == "running"
        events = manager.events(created["run_id"])
        assert [item["event_id"] for item in events["events"]] == [1]
        assert events["events"][0]["source"] == "live"
        assert manager.events(created["run_id"], 1)["events"] == []
        with pytest.raises(RunManagerError, match="invalid_cursor"):
            manager.events(created["run_id"], 100)
        runner.release.set()
        await manager._runs[created["run_id"]].task
        assert manager.status(created["run_id"])["status"] == "completed"
        await manager.close()

    asyncio.run(check())


def test_duplicate_normalized_question_and_paper_order_not_submitted():
    async def check():
        manager = make_manager()
        await manager.create(request("Compare  architecture"))
        duplicate = ResearchRunRequest(
            question=" compare architecture ",
            paper_ids=list(reversed(PAPERS)),
            max_model_calls=50,
        )
        with pytest.raises(RunManagerError, match="duplicate_run"):
            await manager.create(duplicate)
        await manager.close()

    asyncio.run(check())


def test_queue_capacity_and_worker_concurrency():
    async def check():
        runner = FakeRunner()
        manager = make_manager(runner)
        created = [await manager.create(request(f"question {i}")) for i in range(3)]
        with pytest.raises(RunManagerError, match="run_capacity"):
            await manager.create(request("fourth question"))
        await runner.started.wait()
        assert runner.active == 1
        runner.release.set()
        await asyncio.gather(*(manager._runs[item["run_id"]].task for item in created))
        assert runner.peak == 1
        assert len(runner.calls) == 3
        await manager.close()

    asyncio.run(check())


def test_cancel_waits_for_runner_cleanup_and_is_idempotent():
    async def check():
        entered = asyncio.Event()
        cleanup_entered = asyncio.Event()
        release_cleanup = asyncio.Event()

        class CancellableRunner:
            async def run(self, request, emit):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_entered.set()
                    await release_cleanup.wait()
                    raise

        manager = make_manager(CancellableRunner())
        run = await manager.create(request())
        await entered.wait()
        cancellation = await manager.cancel(run["run_id"])
        await cleanup_entered.wait()
        assert cancellation["cancel_requested"]
        assert manager.status(run["run_id"])["status"] == "running"
        await manager.cancel(run["run_id"])
        assert not manager._runs[run["run_id"]].task.done()
        release_cleanup.set()
        await asyncio.gather(manager._runs[run["run_id"]].task, return_exceptions=True)
        assert manager.status(run["run_id"])["status"] == "cancelled"
        await manager.close()

    asyncio.run(check())


def test_cancelling_before_task_starts_never_runs_runner():
    async def check():
        runner = FakeRunner()
        manager = make_manager(runner)
        run = await manager.create(request())
        await manager.cancel(run["run_id"])
        await asyncio.sleep(0)
        assert manager.status(run["run_id"])["status"] == "cancelled"
        assert runner.calls == []
        await manager.close()

    asyncio.run(check())


def test_timeout_and_failure_keep_events_without_exception_text():
    async def check():
        manager = make_manager(timeout_seconds=0.01)
        run = await manager.create(request())
        await manager._runs[run["run_id"]].task
        status = manager.status(run["run_id"])
        assert status["error"]["code"] == "research_timeout"
        assert manager.events(run["run_id"])["events"]
        await manager.close()

        class FailureRunner:
            async def run(self, request, emit):
                emit(
                    node_type="Worker",
                    node_id="worker",
                    status="running",
                    event_type="progress",
                    summary="已有公开事件。",
                    details={
                        "api_key": "private-key",
                        "reasoning": "private reasoning",
                    },
                )
                await asyncio.sleep(0)
                raise RuntimeError("/home/private/log sk-test-should-never-leak")

        manager = make_manager(FailureRunner())
        run = await manager.create(request())
        await manager._runs[run["run_id"]].task
        assert manager.status(run["run_id"])["status"] == "failed"
        assert manager.status(run["run_id"])["error"]["code"] == "execution_failure"
        assert manager.events(run["run_id"])["events"][0]["details"] == {}
        assert "private" not in str(manager.status(run["run_id"]))
        await manager.close()

    asyncio.run(check())


def test_missing_provider_and_invalid_paper_are_explicit():
    async def check():
        manager = make_manager(dependencies=FakeDependencies(False))
        with pytest.raises(RunManagerError, match="research_unavailable"):
            await manager.create(request())
        with pytest.raises(RunManagerError, match="invalid_papers"):
            await manager.create(
                ResearchRunRequest(
                    question="research", paper_ids=[PAPERS[0], "unprepared-paper"]
                )
            )
        assert not manager._runs
        await manager.close()

    asyncio.run(check())


def test_replay_loads_original_report_without_running_research():
    async def check():
        runner = FakeRunner()
        manager = make_manager(runner, FakeDependencies(False))
        run = await manager.replay("g2-1")
        assert run["mode"] == "replay"
        assert run["question"] and len(run["paper_ids"]) == 2
        assert run["historical_status"] == "completed"
        assert runner.calls == []
        events = manager.events(run["run_id"])["events"]
        assert events and all(
            e["source"] == "replay" and e["timestamp"] is None for e in events
        )
        report = manager.report(run["run_id"])
        assert len(report["verified_claims"]) == 4
        assert len(report["missing_requirements"]) == 10
        assert report["human_review"] == "not_performed"
        with pytest.raises(RunManagerError, match="replay_not_found"):
            await manager.replay("unknown")
        await manager.close()

    asyncio.run(check())


def test_terminal_history_bounded_and_close_disables_new_requests():
    async def check():
        manager = make_manager(max_retained_runs=1, max_queued_runs=0)
        first = await manager.replay("g2")
        await manager.replay("g2-1")
        with pytest.raises(RunManagerError, match="run_not_found"):
            manager.status(first["run_id"])
        await manager.close()
        with pytest.raises(RunManagerError, match="service_stopping"):
            await manager.create(request())

    asyncio.run(check())


def test_replay_cannot_evict_active_run_or_exceed_retention():
    async def check():
        manager = make_manager(max_retained_runs=1, max_queued_runs=0)
        run = await manager.create(request())
        with pytest.raises(RunManagerError, match="history_capacity"):
            await manager.replay("g2")
        assert len(manager._runs) == 1
        assert manager.status(run["run_id"])["status"] in {"queued", "running"}
        await manager.close()

    asyncio.run(check())


def test_timeout_keeps_actual_partial_snapshot_and_costs():
    async def check():
        class PartialRunner:
            async def run(self, request, emit):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    exc.evaluation_snapshot = {
                        "question": request.question,
                        "paper_ids": request.paper_ids,
                        "model_calls_observed": 2,
                        "llm_calls_observed": 2,
                        "vlm_calls_observed": 0,
                        "research": {
                            "status": "cancelled",
                            "initial_worker_results": [],
                        },
                    }
                    raise

        manager = make_manager(PartialRunner(), timeout_seconds=0.01)
        run = await manager.create(request())
        await manager._runs[run["run_id"]].task
        status = manager.status(run["run_id"])
        assert status["status"] == "failed"
        assert status["error"]["code"] == "research_timeout"
        assert status["metrics"]["model_calls"] == 2
        assert status["details_available"] is True
        assert status["report_available"] is False
        assert manager.report(run["run_id"])["verified_claims"] == []
        await manager.close()

    asyncio.run(check())


@pytest.mark.parametrize(
    "changes",
    [
        {"question": " "},
        {"paper_ids": [PAPERS[0]]},
        {"paper_ids": PAPERS + ["third", "fourth"]},
        {"paper_ids": [PAPERS[0], PAPERS[0]]},
        {"max_model_calls": True},
        {"max_model_calls": 201},
        {"max_model_calls": 0},
        {"provider_url": "https://example.com"},
        {"output_path": "/tmp/user-controlled"},
    ],
)
def test_request_contract_rejects_unsafe_or_invalid_arguments(changes):
    with pytest.raises(ValidationError):
        ResearchRunRequest.model_validate(
            {"question": "compare", "paper_ids": PAPERS, **changes}
        )
