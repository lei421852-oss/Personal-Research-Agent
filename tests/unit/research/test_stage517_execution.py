"""研究执行资格：无权启动时不调用 Runner 或改写持久终态。"""

import asyncio

import pytest

from app.persistence.repository import TransitionOutcome
from tests.unit.research.test_lifecycle import (
    HappyRunner,
    Recorder,
    manager,
    request,
    settle,
)


@pytest.mark.parametrize(
    "result,expected_calls",
    [
        (TransitionOutcome.UPDATED, 1),
        (TransitionOutcome.NOT_FOUND, 0),
        (TransitionOutcome.VERSION_CONFLICT, 0),
        (TransitionOutcome.STATUS_CONFLICT, 0),
        (TransitionOutcome.OWNER_CONFLICT, 0),
        (RuntimeError("database failure"), 0),
    ],
)
def test_runner_requires_confirmed_running_transition(result, expected_calls):
    class Lifecycle(Recorder):
        async def mark_running(self, run_id):
            if isinstance(result, Exception):
                raise result
            return result

    async def go():
        store = Lifecycle()
        runner = HappyRunner()
        mgr = manager(runner, store)
        try:
            created = await mgr.create(request())
            await settle(mgr, created["run_id"])
            assert runner.calls == expected_calls
            if expected_calls == 0:
                assert store.calls == []
        finally:
            await mgr.close()

    asyncio.run(go())


@pytest.mark.parametrize(
    "owner,status,outcome,expected",
    [
        ("a" * 32, "queued", TransitionOutcome.UPDATED, "updated"),
        ("b" * 32, "queued", TransitionOutcome.UPDATED, "owner_conflict"),
        ("a" * 32, "running", TransitionOutcome.UPDATED, "status_conflict"),
        ("a" * 32, "queued", TransitionOutcome.VERSION_CONFLICT, "version_conflict"),
    ],
)
def test_lifecycle_checks_owner_and_transition_outcome(
    owner, status, outcome, expected
):
    from types import SimpleNamespace

    from app.persistence.lifecycle import RunPersistence
    from app.persistence.repository import TransitionResult

    class Repo:
        def __init__(self):
            self.calls = []

        async def get_run(self, run_id):
            return SimpleNamespace(
                owner_instance_id=owner, status=status, state_version=0
            )

        async def transition_run(self, run_id, **kwargs):
            self.calls.append(kwargs)
            return TransitionResult(outcome)

    async def go():
        repo = Repo()
        lifecycle = RunPersistence(repo, owner_instance_id="a" * 32)
        result = await lifecycle.mark_running("c" * 32)
        assert result.value == expected
        if expected in {"updated", "version_conflict"}:
            assert repo.calls[0]["expected_owner_id"] == "a" * 32
        else:
            assert repo.calls == []

    asyncio.run(go())


def test_finish_wrong_owner_does_not_write_terminal():
    from types import SimpleNamespace

    from app.persistence.lifecycle import RunPersistence
    from app.persistence.repository import ConflictError

    class Repo:
        writes = 0

        async def get_run(self, run_id):
            return SimpleNamespace(owner_instance_id="b" * 32)

        async def finalize_run(self, *args, **kwargs):
            self.writes += 1

    async def go():
        repo = Repo()
        lifecycle = RunPersistence(repo, owner_instance_id="a" * 32)
        with pytest.raises(ConflictError, match="owner_conflict"):
            await lifecycle.finish("c" * 32, {"status": "failed"})
        assert repo.writes == 0

    asyncio.run(go())


def test_discard_closes_only_local_writer():
    from app.persistence.lifecycle import RunPersistence

    class Writer:
        closed = False

        async def close(self):
            self.closed = True

    async def go():
        lifecycle = RunPersistence(object(), owner_instance_id="a" * 32)
        writer = Writer()
        lifecycle._writers["c" * 32] = writer
        await lifecycle.discard("c" * 32)
        assert writer.closed and lifecycle._writers == {}

    asyncio.run(go())


def test_unstarted_cancel_does_not_finalize_an_already_running_run():
    from types import SimpleNamespace

    from app.persistence.lifecycle import RunPersistence
    from app.persistence.repository import ConflictError

    class Repo:
        writes = 0

        async def get_run(self, run_id):
            return SimpleNamespace(owner_instance_id="a" * 32, status="running")

        async def finalize_run(self, *args, **kwargs):
            self.writes += 1

    async def go():
        repo = Repo()
        lifecycle = RunPersistence(repo, owner_instance_id="a" * 32)
        with pytest.raises(ConflictError, match="unclaimed_run_not_queued"):
            await lifecycle.finish(
                "c" * 32, {"status": "cancelled", "finalize_requires_queued": True}
            )
        assert repo.writes == 0

    asyncio.run(go())


def test_loser_discard_does_not_close_shared_active_writer():
    from app.persistence.lifecycle import RunPersistence

    class Writer:
        closed = False

        async def close(self):
            self.closed = True

    async def go():
        lifecycle = RunPersistence(object(), owner_instance_id="a" * 32)
        writer = Writer()
        lifecycle._writers["c" * 32] = writer
        lifecycle._active_run_ids.add("c" * 32)
        await lifecycle.discard("c" * 32)
        assert not writer.closed
        assert lifecycle._writers["c" * 32] is writer

    asyncio.run(go())


def test_discard_waits_for_committed_claim_to_be_registered():
    from types import SimpleNamespace

    from app.persistence.lifecycle import RunPersistence
    from app.persistence.repository import TransitionResult

    class Repo:
        status = "queued"
        committed = asyncio.Event()
        release = asyncio.Event()

        async def get_run(self, run_id):
            return SimpleNamespace(
                owner_instance_id="a" * 32, status=self.status, state_version=0
            )

        async def transition_run(self, run_id, **kwargs):
            self.status = "running"
            self.committed.set()
            await self.release.wait()
            return TransitionResult(TransitionOutcome.UPDATED)

    class Writer:
        closed = False

        async def close(self):
            self.closed = True

    async def go():
        repo = Repo()
        lifecycle = RunPersistence(repo, owner_instance_id="a" * 32)
        writer = Writer()
        lifecycle._writers["c" * 32] = writer
        winner = asyncio.create_task(lifecycle.mark_running("c" * 32))
        await repo.committed.wait()

        async def lose():
            assert (
                await lifecycle.mark_running("c" * 32)
                is TransitionOutcome.STATUS_CONFLICT
            )
            await lifecycle.discard("c" * 32)

        loser = asyncio.create_task(lose())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        was_closed = writer.closed
        repo.release.set()
        await asyncio.gather(winner, loser)
        assert not was_closed and not writer.closed
        assert lifecycle._writers["c" * 32] is writer

    asyncio.run(go())
