"""数据库错误与重放安全的确定性验证。"""

import asyncio

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.persistence.coordinator import PersistenceLatch
from app.persistence.errors import StorageError
from app.persistence.writer import RunWriter, WriterFailed
from tests.unit.persistence.test_database import resource
from tests.unit.persistence.test_writer import FakeRepository, append


class DriverError(Exception):
    def __init__(self, state):
        self.sqlstate = state


def db_error(state):
    return DBAPIError(None, None, DriverError(state))


@pytest.mark.parametrize(
    "state,safe",
    [
        ("23502", False),
        ("23503", False),
        ("23505", False),
        ("23514", False),
        ("40001", True),
        ("40P01", True),
        ("08006", False),
        ("55P03", False),
    ],
)
def test_database_error_classification(state, safe):
    async def go():
        db, _ = resource()
        await db.start()
        try:
            with pytest.raises(StorageError) as caught:
                async with db.session():
                    raise db_error(state)
            assert caught.value.sqlstate == state
            assert caught.value.retryable is safe
            assert caught.value.replay_safe is safe
        finally:
            await db.close()

    asyncio.run(go())


def test_cleanup_failure_prevents_retry():
    async def go():
        db, _ = resource()
        await db.start()
        try:
            with pytest.raises(StorageError) as caught:
                async with db.session() as session:
                    session.rollback_error = RuntimeError("cleanup")
                    raise db_error("40001")
            assert not caught.value.retryable
            assert not caught.value.replay_safe
        finally:
            await db.close()

    asyncio.run(go())


def test_pool_timeout_before_checkout_can_retry():
    async def go():
        db, _ = resource()
        await db.start()
        try:
            with pytest.raises(StorageError) as caught:
                async with db.session():
                    raise PoolTimeout("timeout")
            assert caught.value.code == "storage_pool_timeout"
            assert caught.value.retryable and caught.value.replay_safe
        finally:
            await db.close()

    asyncio.run(go())


@pytest.mark.parametrize("state", ["23503", "23505", "23514", "08006"])
def test_writer_never_replays_unsafe_failure(state):
    async def go():
        repo = FakeRepository()
        repo.fail_times = 1
        repo.error = StorageError(
            "storage_unavailable", retryable=True, sqlstate=state, replay_safe=True
        )
        latch = PersistenceLatch()
        writer = RunWriter(repo, latch=latch)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert writer.stats.retried == 0 and latch.tripped
            assert not repo.appends
        finally:
            await writer.close()

    asyncio.run(go())


def test_writer_timeout_does_not_replay_unknown_commit():
    async def go():
        repo = FakeRepository()
        repo.gate = asyncio.Event()
        latch = PersistenceLatch()
        writer = RunWriter(repo, latch=latch, write_timeout=0.02)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert writer.stats.retried == 0 and latch.tripped
        finally:
            await writer.close()

    asyncio.run(go())


@pytest.mark.parametrize("state", ["40001", "40P01"])
def test_writer_retries_confirmed_rolled_back_transactions(state):
    async def go():
        repo = FakeRepository()
        repo.fail_times = 1
        repo.error = StorageError(
            "storage_unavailable", retryable=True, sqlstate=state, replay_safe=True
        )
        writer = RunWriter(repo, latch=PersistenceLatch())
        writer.start()
        try:
            writer.submit(append(1, 0))
            await writer.flush()
            assert writer.stats.retried == 1
            assert repo.appends == [[1]]
        finally:
            await writer.close()

    asyncio.run(go())
