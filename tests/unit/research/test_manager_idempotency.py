"""R4 幂等与重放分支（离线、Fake lifecycle）。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.persistence.lifecycle import key_hash, request_hash
from app.persistence.repository import RunRecord
from app.research.run_manager import MainAgentRunRequest, ResearchRunManager

PAPERS = ["clip-x", "blip-y"]
PAYLOAD = {
    "question": "q",
    "paper_ids": PAPERS,
    "language_mode": "auto",
    "max_model_calls": 200,
}


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


class FakeLifecycle:
    def __init__(self, *, existing=None, start_replayed=False, match_hash=True):
        self.existing = existing
        self.start_replayed = start_replayed
        self.match_hash = match_hash
        self.start_calls = []
        self.finished = []

    async def start(
        self,
        *,
        run_id,
        payload,
        fingerprint,
        created_at=None,
        idempotency_key_hash=None,
    ):
        self.start_calls.append(idempotency_key_hash)
        if self.start_replayed:
            return type("Started", (), {"replayed": True, "record": self.existing})()
        return type("Started", (), {"replayed": False, "record": None})()

    async def mark_running(self, run_id):
        return None

    def on_event(self, run_id, event):
        return None

    async def finish(self, run_id, snapshot):
        self.finished.append(run_id)

    async def close(self):
        return None

    async def find_by_key(self, digest):
        if self.existing is None:
            return None
        # 同请求分支用本次请求哈希构造记录；冲突分支保留原值。
        return self.existing


def record(request_digest):
    now = datetime.now(UTC)
    return RunRecord(
        run_id="a" * 32,
        mode="live",
        status="completed",
        state_version=2,
        last_event_seq=3,
        current_report_revision=None,
        created_at=now,
        started_at=now,
        finished_at=now,
        error=None,
        metrics={},
        cancel_requested=False,
        event_limit_reached=False,
        public_state={},
        effective_paper_ids=[],
        finalize_token=None,
        finalize_sha256=None,
        request_hash=request_digest,
    )


def manager(lifecycle):
    runner = Runner()
    mgr = ResearchRunManager(runner, Deps(), Replays(), lifecycle=lifecycle)
    return mgr, runner


def request(question="q"):
    return MainAgentRunRequest(question=question, paper_ids=PAPERS)


def run(coro):
    return asyncio.run(coro)


def digest_for(payload):
    return request_hash(payload)


def test_same_key_same_request_replays_without_task():
    async def body():
        lifecycle = FakeLifecycle(existing=record(digest_for(PAYLOAD)))
        mgr, runner = manager(lifecycle)
        result = await mgr.create(request(), idempotency_key="k1")
        return result, runner.calls, len(mgr._runs)

    result, calls, runs = run(body())
    assert result["replayed"] is True
    assert result["run_id"] == "a" * 32
    assert calls == 0
    assert runs == 0


def test_same_key_different_request_conflicts():
    async def body():
        lifecycle = FakeLifecycle(existing=record("0" * 64), match_hash=False)
        mgr, _ = manager(lifecycle)
        try:
            await mgr.create(request(), idempotency_key="k1")
        except Exception as exc:  # noqa: BLE001
            return exc.code, exc.status_code
        return None, None

    code, status = run(body())
    assert code == "idempotency_key_conflict"
    assert status == 409


def test_replay_only_without_record_returns_404():
    async def body():
        lifecycle = FakeLifecycle(existing=None)
        mgr, runner = manager(lifecycle)
        try:
            await mgr.create(request(), idempotency_key="k1", replay_only=True)
        except Exception as exc:  # noqa: BLE001
            return exc.code, runner.calls
        return None, None

    code, calls = run(body())
    assert code == "submission_not_found"
    assert calls == 0


def test_new_key_is_forwarded_to_persistence():
    async def body():
        lifecycle = FakeLifecycle()
        mgr, _ = manager(lifecycle)
        await mgr.create(request(), idempotency_key="k2")
        task = next(iter(mgr._runs.values())).task
        await asyncio.wait_for(task, 3)
        return lifecycle.start_calls

    assert run(body()) == [key_hash("k2")]


def test_replay_only_without_persistence_is_404():
    async def body():
        mgr, runner = manager(None)
        try:
            await mgr.create(request(), idempotency_key="k4", replay_only=True)
        except Exception as exc:  # noqa: BLE001
            return exc.code, runner.calls
        return None, None

    code, calls = run(body())
    assert code == "submission_not_found"
    assert calls == 0
