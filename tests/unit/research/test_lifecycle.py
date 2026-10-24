"""批次 C：Manager 生命周期与事件屏障的确定性测试（Fake Runner / Fake lifecycle）。"""

from __future__ import annotations

import asyncio
import contextlib

from app.research.run_manager import (
    MainAgentRunRequest,
    ResearchRunManager,
)

PAPERS = ["clip-x", "blip-y"]


class FakeDependencies:
    def papers(self):
        return [{"paper_id": paper_id} for paper_id in PAPERS]

    def health(self):
        return {
            "real_research_ready": True,
            "language_adapter_ready": True,
        }


class FakeReplays:
    def list_cases(self):
        return []


class Recorder:
    """记录收尾调用；可选阻塞或失败，用于验证屏障与超时。"""

    def __init__(self, *, gate: asyncio.Event | None = None, error=None):
        self.calls: list[dict] = []
        self.closed = False
        self.gate = gate
        self.error = error

    async def finish(self, run_id, snapshot):
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        self.calls.append({"run_id": run_id, **snapshot})

    async def close(self):
        self.closed = True


class HappyRunner:
    main_agent_entry = False

    def __init__(self):
        self.calls = 0

    async def run(self, request, sink):
        self.calls += 1
        sink(
            node_type="MainAgent",
            node_id="main",
            status="running",
            event_type="started",
            summary="开始",
            details={},
        )
        return {"research": {"status": "completed"}, "status": "completed"}


class FailingRunner:
    main_agent_entry = False

    async def run(self, request, sink):
        raise RuntimeError("runner exploded")


class BlockingRunner:
    main_agent_entry = False

    def __init__(self):
        self.started = asyncio.Event()

    async def run(self, request, sink):
        self.started.set()
        await asyncio.Event().wait()


def manager(runner, lifecycle=None, **kwargs):
    return ResearchRunManager(
        runner,
        FakeDependencies(),
        FakeReplays(),
        lifecycle=lifecycle,
        **kwargs,
    )


def request(question="q"):
    return MainAgentRunRequest(question=question, paper_ids=PAPERS)


def run(coro):
    return asyncio.run(coro)


async def settle(manager_, run_id, *, wait_task=True, wait_finalize=True):
    """先等研究任务结束，再等 done callback 登记并完成收尾任务。"""

    run_state = manager_._runs[run_id]
    if wait_task and run_state.task is not None:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(run_state.task), 5)
    for _ in range(20):
        await asyncio.sleep(0)
        if run_state.finalize_task is not None:
            break
    if wait_finalize and run_state.finalize_task is not None:
        await asyncio.wait_for(asyncio.shield(run_state.finalize_task), 5)


def test_normal_completion_finishes_exactly_once():
    async def body():
        recorder = Recorder()
        mgr = manager(HappyRunner(), recorder)
        created = await mgr.create(request())
        await settle(mgr, created["run_id"])
        await mgr.close()
        return recorder.calls, mgr._runs[created["run_id"]].persisted

    calls, persisted = run(body())
    assert len(calls) == 1
    assert calls[0]["status"] == "completed"
    assert persisted is True


def test_queued_cancel_is_finalized_by_managed_task():
    async def body():
        recorder = Recorder()
        runner = BlockingRunner()
        mgr = manager(runner, recorder, max_concurrent_runs=1, max_queued_runs=1)
        first = await mgr.create(request())
        await runner.started.wait()
        second = await mgr.create(request("q2"))
        await mgr.cancel(second["run_id"])
        await settle(mgr, second["run_id"])
        await mgr.cancel(first["run_id"])
        await settle(mgr, first["run_id"])
        await mgr.close()
        return recorder.calls, {first["run_id"], second["run_id"]}

    calls, expected = run(body())
    assert {call["run_id"] for call in calls} == expected
    assert all(call["status"] == "cancelled" for call in calls)


def test_late_events_after_ingress_close_are_counted_not_appended():
    async def body():
        recorder = Recorder()
        mgr = manager(HappyRunner(), recorder)
        created = await mgr.create(request())
        run_state = mgr._runs[created["run_id"]]
        await settle(mgr, created["run_id"])
        before = len(run_state.events)
        run_state.ingress_closed = True
        sink = mgr._sink(run_state)
        sink(
            node_type="MainAgent",
            node_id="late",
            status="running",
            event_type="progress",
            summary="迟到",
            details={},
        )
        await asyncio.sleep(0)
        await mgr.close()
        return before, len(run_state.events), run_state.late_events

    before, after, late = run(body())
    assert after == before
    assert late == 1


def test_accepted_callback_is_drained_before_finish():
    async def body():
        recorder = Recorder()
        mgr = manager(HappyRunner(), recorder)
        created = await mgr.create(request())
        await settle(mgr, created["run_id"])
        await mgr.close()
        return recorder.calls[0]["events"]

    # runner 内 emit 的那条事件必须在收尾前写入内存事件流。
    assert run(body()) >= 1


def test_finish_failure_keeps_run_unpersisted():
    class Boom(Exception):
        code = "storage_unavailable"

    async def body():
        recorder = Recorder(error=Boom("x"))
        mgr = manager(HappyRunner(), recorder)
        created = await mgr.create(request())
        await settle(mgr, created["run_id"])
        run_state = mgr._runs[created["run_id"]]
        await mgr.close()
        return run_state.persisted, run_state.persistence_error, run_state.status

    persisted, error, status = run(body())
    assert persisted is False
    assert error == "storage_unavailable"
    assert status == "completed"  # 内存状态正确，但不宣称已持久化


def test_finalize_timeout_is_reported_on_close():
    async def body():
        gate = asyncio.Event()
        recorder = Recorder(gate=gate)
        mgr = manager(HappyRunner(), recorder, finalize_timeout=0.05)
        created = await mgr.create(request())
        await settle(mgr, created["run_id"], wait_task=False, wait_finalize=False)
        await mgr.close()
        run_state = mgr._runs[created["run_id"]]
        # 收尾任务自身的 wait_for 会超时并记录明确错误，不能静默丢弃。
        await asyncio.wait_for(asyncio.shield(run_state.finalize_task), 5)
        return run_state.persistence_error, run_state.persisted

    error, persisted = run(body())
    assert error == "finalize_timeout"
    assert persisted is False


def test_manager_close_waits_for_finalizer_and_closes_lifecycle():
    async def body():
        recorder = Recorder()
        mgr = manager(HappyRunner(), recorder)
        await mgr.create(request())
        await mgr.close()
        return recorder.closed, len(recorder.calls)

    closed, calls = run(body())
    assert closed is True
    assert calls == 1


def test_runner_exception_still_finalizes_once():
    async def body():
        recorder = Recorder()
        mgr = manager(FailingRunner(), recorder)
        created = await mgr.create(request())
        await asyncio.wait_for(mgr._runs[created["run_id"]].task, 5)
        await settle(mgr, created["run_id"])
        await mgr.close()
        return recorder.calls

    calls = run(body())
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"


def test_single_finalize_task_per_run():
    async def body():
        recorder = Recorder()
        mgr = manager(HappyRunner(), recorder)
        created = await mgr.create(request())
        await settle(mgr, created["run_id"])
        await mgr.cancel(created["run_id"])  # 终态后再次取消不应产生第二个收尾
        await asyncio.sleep(0)
        tasks = [task for task in mgr._finalize_tasks.values()]
        await mgr.close()
        return len(tasks), len(recorder.calls)

    tasks, calls = run(body())
    assert tasks == 1
    assert calls == 1


def test_finalize_does_not_run_without_lifecycle():
    async def body():
        mgr = manager(HappyRunner())
        created = await mgr.create(request())
        await asyncio.wait_for(mgr._runs[created["run_id"]].task, 5)
        await asyncio.sleep(0)
        await mgr.close()
        return mgr._finalize_tasks, mgr._runs[created["run_id"]].persisted

    tasks, persisted = run(body())
    assert tasks == {}
    assert persisted is False
