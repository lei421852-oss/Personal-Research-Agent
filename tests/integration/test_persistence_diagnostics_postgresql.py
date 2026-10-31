"""仅允许一次性隔离 PostgreSQL 的诊断集成测试。"""

import asyncio
import os

import pytest
from sqlalchemy.engine import make_url

from app.persistence.coordinator import PersistenceLatch
from app.persistence.repository import ConflictError, EventCommand, ResearchRepository
from app.persistence.writer import AppendEvents, RunWriter, WriterFailed
from tests.integration.test_writer_postgresql import cleanup, create_run, resource

URL = os.environ.get("H2_TEST_PERSISTENCE_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("H2_TEST_PERSISTENCE_AUTHORIZED") != "yes" or not URL,
    reason="需要专用隔离库和显式授权",
)


def check_target():
    url = make_url(URL)
    assert (url.drivername, url.host, url.port, url.database, url.username) == (
        "postgresql+asyncpg",
        "127.0.0.1",
        55438,
        "latch_test",
        "h2_test",
    )
    assert dict(url.query) == {"ssl": "disable"}


def test_real_postgres_constraint_sqlstate_and_rollback(monkeypatch):
    check_target()
    emitted = []
    monkeypatch.setattr(
        "app.persistence.writer.emit_failure", lambda item: emitted.append(item)
    )

    async def scenario():
        res = resource()
        await res.start()
        writer = None
        try:
            repo = ResearchRepository(res)
            identity = "c" * 32
            await create_run(repo, identity, "7" * 64)
            latch = PersistenceLatch()
            writer = RunWriter(repo, latch=latch)
            writer.start()
            item = EventCommand.build(
                seq=1,
                node_type="InvalidTestNode",
                node_id="test",
                status="running",
                event_type="started",
                source="live",
                summary="fixture",
            )
            writer.submit(AppendEvents(identity, (item,), 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert latch.diagnostic["sqlstate"] == "23514"
            assert latch.diagnostic["category"] == "database_constraint_error"
            assert len(emitted) == 1
            assert await repo.get_events(identity) == []
            assert (await repo.get_run(identity)).last_event_seq == 0
        finally:
            if writer:
                await writer.close()
            await cleanup(res, [identity])
            await res.close()

    asyncio.run(scenario())


def test_cursor_conflict_records_first_failure(monkeypatch):
    check_target()
    emitted = []
    monkeypatch.setattr(
        "app.persistence.writer.emit_failure", lambda item: emitted.append(item)
    )

    async def scenario():
        res = resource()
        await res.start()
        writer = None
        try:
            repo = ResearchRepository(res)
            identity = "d" * 32
            await create_run(repo, identity, "8" * 64)
            latch = PersistenceLatch()
            writer = RunWriter(repo, latch=latch)
            writer.start()
            item = EventCommand.build(
                seq=1,
                node_type="MainAgent",
                node_id="test",
                status="running",
                event_type="started",
                source="live",
                summary="fixture",
            )
            writer.submit(AppendEvents(identity, (item,), 5))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert isinstance(writer.failure, ConflictError)
            assert latch.diagnostic["internal_error_code"] == "event_cursor_conflict"
            assert latch.diagnostic["operation"] == "append_events"
            assert len(emitted) == 1
            assert await repo.get_events(identity) == []
        finally:
            if writer:
                await writer.close()
            await cleanup(res, [identity])
            await res.close()

    asyncio.run(scenario())
