"""终态 single-flight 与核对必须绑定同一 token/内容哈希。"""

import asyncio
from dataclasses import replace

import pytest

from app.persistence.coordinator import TerminalCoordinator
from app.persistence.repository import (
    FinalizeOutcome,
    ReconcileOutcome,
    RepositoryError,
)
from tests.unit.persistence.test_coordinator import FakeTerminalRepository, command


def test_inflight_rejects_other_token_and_hash():
    async def scenario():
        repo = FakeTerminalRepository()
        repo.gate = asyncio.Event()
        coordinator = TerminalCoordinator(repo)
        original = command()
        first = asyncio.create_task(coordinator.finalize(original))
        await asyncio.sleep(0)
        for changed in (command("c" * 32), replace(original, finalize_sha256="d" * 64)):
            with pytest.raises(RepositoryError):
                await asyncio.wait_for(coordinator.finalize(changed), timeout=0.1)
        second = asyncio.create_task(coordinator.finalize(original))
        repo.gate.set()
        assert await first is FinalizeOutcome.NEW
        assert await second in (FinalizeOutcome.NEW, FinalizeOutcome.ALREADY_COMMITTED)
        assert repo.finalize_calls == 1

    asyncio.run(scenario())


def test_committed_does_not_confirm_wrong_identity():
    async def scenario():
        repo = FakeTerminalRepository()
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        await coordinator.finalize(cmd)
        for token, sha in (
            ("c" * 32, cmd.finalize_sha256),
            (cmd.finalize_token, "d" * 64),
        ):
            assert (
                await coordinator.reconcile(cmd.run_id, token=token, sha256=sha)
                is not ReconcileOutcome.CONFIRMED
            )
        assert (
            await coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
            is ReconcileOutcome.CONFIRMED
        )

    asyncio.run(scenario())


def test_committed_finalize_rejects_same_token_other_hash():
    async def scenario():
        repo = FakeTerminalRepository()
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        await coordinator.finalize(cmd)
        with pytest.raises(RepositoryError):
            await coordinator.finalize(replace(cmd, finalize_sha256="d" * 64))
        assert repo.finalize_calls == 1

    asyncio.run(scenario())


def test_foreign_terminal_is_not_confirmed_as_own_commit():
    async def scenario():
        repo = FakeTerminalRepository([FinalizeOutcome.FOREIGN_TERMINAL])
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        assert await coordinator.finalize(cmd) is FinalizeOutcome.FOREIGN_TERMINAL
        assert (
            await coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
            is ReconcileOutcome.FOREIGN_TERMINAL
        )
        assert await coordinator.finalize(cmd) is FinalizeOutcome.FOREIGN_TERMINAL
        assert repo.finalize_calls == 1

    asyncio.run(scenario())


def test_same_identity_different_trailing_events_rejected():
    from tests.unit.persistence.test_writer import event

    async def scenario():
        repo = FakeTerminalRepository()
        repo.gate = asyncio.Event()
        coordinator = TerminalCoordinator(repo)
        cmd = replace(command(), final_last_event_seq=1)
        original = event(1)
        changed = replace(original, summary="different")
        first = asyncio.create_task(
            coordinator.finalize(cmd, trailing_events=[original])
        )
        await asyncio.sleep(0)
        with pytest.raises(RepositoryError):
            await asyncio.wait_for(
                coordinator.finalize(cmd, trailing_events=[changed]), timeout=0.1
            )
        repo.gate.set()
        assert await first is FinalizeOutcome.NEW
        with pytest.raises(RepositoryError):
            await coordinator.finalize(cmd, trailing_events=[changed])
        assert repo.finalize_calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "verification,expected",
    [
        (False, ReconcileOutcome.CONTENT_CONFLICT),
        (None, ReconcileOutcome.UNAVAILABLE),
        (True, ReconcileOutcome.CONFIRMED),
    ],
)
def test_unknown_nonempty_tail_requires_readonly_verification(verification, expected):
    from tests.unit.persistence.test_writer import event

    class Repo(FakeTerminalRepository):
        def __init__(self):
            super().__init__([FinalizeOutcome.UNKNOWN], [ReconcileOutcome.CONFIRMED])
            self.checked = 0

        async def verify_trailing_events(self, run_id, expected_events):
            self.checked += 1
            assert len(expected_events) == 1
            return verification

    async def scenario():
        repo = Repo()
        coordinator = TerminalCoordinator(repo)
        cmd = replace(command(), final_last_event_seq=1)
        assert (
            await coordinator.finalize(cmd, trailing_events=[event(1)])
            is FinalizeOutcome.UNKNOWN
        )
        actual = await coordinator.reconcile(
            cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
        )
        assert actual is expected and repo.checked == 1
        assert coordinator.latch.tripped is (verification is not True)

    asyncio.run(scenario())


def test_fresh_coordinator_cannot_confirm_missing_tail_snapshot():
    from app.persistence.coordinator import PersistenceLatched, TerminalPhase

    async def scenario():
        repo = FakeTerminalRepository(reconciles=[ReconcileOutcome.CONFIRMED])
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        result = await coordinator.reconcile(
            cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
        )
        assert result is ReconcileOutcome.STILL_PENDING
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.UNKNOWN
        assert coordinator.latch.tripped
        with pytest.raises(PersistenceLatched):
            await coordinator.finalize(command("c" * 32))

    asyncio.run(scenario())


def test_other_identity_confirmed_without_snapshot_is_not_confirmed():
    from app.persistence.coordinator import TerminalPhase

    async def scenario():
        repo = FakeTerminalRepository(
            [FinalizeOutcome.UNKNOWN], [ReconcileOutcome.CONFIRMED]
        )
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        await coordinator.finalize(cmd)
        result = await coordinator.reconcile(
            cmd.run_id, token="c" * 32, sha256="d" * 64
        )
        assert result is ReconcileOutcome.STILL_PENDING
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.UNKNOWN
        assert coordinator.latch.tripped

    asyncio.run(scenario())


def test_reconcile_during_inflight_does_not_latch_or_change_phase():
    from app.persistence.coordinator import TerminalPhase

    async def scenario():
        repo = FakeTerminalRepository()
        repo.gate = asyncio.Event()
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        pending = asyncio.create_task(coordinator.finalize(cmd))
        await asyncio.sleep(0)
        assert (
            await coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
            is ReconcileOutcome.STILL_PENDING
        )
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.IN_FLIGHT
        assert not coordinator.latch.tripped
        repo.gate.set()
        assert await pending is FinalizeOutcome.NEW
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.COMMITTED
        assert not coordinator.latch.tripped
        assert repo.reconcile_calls == 0

    asyncio.run(scenario())


def test_concurrent_reconcile_cannot_downgrade_confirmed_state():
    from app.persistence.coordinator import TerminalPhase
    from app.persistence.repository import ReconcileResult

    class Repo(FakeTerminalRepository):
        def __init__(self):
            super().__init__([FinalizeOutcome.UNKNOWN])
            self.entered = 0
            self.both = asyncio.Event()
            self.release_pending = asyncio.Event()

        async def reconcile_finalize(self, *args, **kwargs):
            self.entered += 1
            number = self.entered
            if number == 2:
                self.both.set()
            await self.both.wait()
            if number == 1:
                await self.release_pending.wait()
                return ReconcileResult(ReconcileOutcome.STILL_PENDING)
            return ReconcileResult(ReconcileOutcome.CONFIRMED)

    async def scenario():
        repo = Repo()
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        await coordinator.finalize(cmd)
        first = asyncio.create_task(
            coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
        )
        second = asyncio.create_task(
            coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
        )
        await repo.both.wait()
        assert await second is ReconcileOutcome.CONFIRMED
        repo.release_pending.set()
        assert await first is ReconcileOutcome.CONFIRMED
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.COMMITTED
        assert not coordinator.latch.tripped

    asyncio.run(scenario())


def test_idle_reconcile_cannot_override_new_inflight_finalize():
    from app.persistence.coordinator import TerminalPhase
    from app.persistence.repository import ReconcileResult

    class Repo(FakeTerminalRepository):
        def __init__(self):
            super().__init__()
            self.read_started = asyncio.Event()
            self.read_release = asyncio.Event()
            self.gate = asyncio.Event()

        async def reconcile_finalize(self, *args, **kwargs):
            self.read_started.set()
            await self.read_release.wait()
            return ReconcileResult(ReconcileOutcome.STILL_PENDING)

    async def scenario():
        repo = Repo()
        coordinator = TerminalCoordinator(repo)
        cmd = command()
        read = asyncio.create_task(
            coordinator.reconcile(
                cmd.run_id, token=cmd.finalize_token, sha256=cmd.finalize_sha256
            )
        )
        await repo.read_started.wait()
        write = asyncio.create_task(coordinator.finalize(cmd))
        await asyncio.sleep(0)
        repo.read_release.set()
        assert await read is ReconcileOutcome.STILL_PENDING
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.IN_FLIGHT
        assert not coordinator.latch.tripped
        repo.gate.set()
        assert await write is FinalizeOutcome.NEW
        assert coordinator.record(cmd.run_id).phase is TerminalPhase.COMMITTED
        assert not coordinator.latch.tripped

    asyncio.run(scenario())
