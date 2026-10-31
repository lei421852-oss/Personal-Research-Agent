import asyncio

import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.errors import StorageError
from tests.unit.persistence.fakes import Factories

URL = "postgresql+asyncpg://reader:fixture-password@localhost/personal_agent_test_unit?ssl=disable"


def resource(factories=None, **settings):
    factories = factories or Factories()
    return DatabaseResource(
        DatabaseSettings(enabled=True, url=URL, **settings),
        engine_factory=factories.create_engine,
        session_factory_builder=factories.make_sessions,
    ), factories


def test_engine_is_lifespan_scoped_and_settings_are_explicit():
    async def run():
        db, f = resource()
        assert f.engine_calls == []
        await db.start()
        await db.start()
        assert len(f.engine_calls) == 1
        assert f.engine.opened == 0
        options = f.engine_calls[0][1]
        assert options["pool_size"] == 2 and options["max_overflow"] == 0
        assert options["pool_pre_ping"] and options["hide_parameters"]
        assert not options["echo"] and not options["echo_pool"]
        assert f.session_options["expire_on_commit"] is False
        assert f.session_options["autoflush"] is False
        await db.close()
        await db.close()
        assert f.engine.disposed == 1

    asyncio.run(run())


def test_sessions_are_independent_rollback_without_autocommit():
    async def run():
        db, f = resource()
        await db.start()
        async with db.session() as first, db.session() as second:
            assert first is not second
        assert all(s.closed == s.rollbacks == 1 and s.commits == 0 for s in f.sessions)
        assert not db._leases
        await db.close()

    asyncio.run(run())


def test_session_error_rolls_back_and_preserves_business_error():
    async def run():
        db, f = resource()
        await db.start()
        with pytest.raises(ValueError, match="business failure"):
            async with db.session():
                raise ValueError("business failure")
        assert f.sessions[0].closed == f.sessions[0].rollbacks == 1
        await db.close()

    asyncio.run(run())


def test_request_cancellation_waits_for_session_cleanup():
    async def run():
        db, f = resource()
        await db.start()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def request():
            async with db.session() as session:
                session.close_release = release
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(request())
        await entered.wait()
        task.cancel()
        await f.sessions[0].close_started.wait()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert f.sessions[0].closed == f.sessions[0].rollbacks == 1
        await db.close()

    asyncio.run(run())


def test_close_releases_outstanding_owned_session_and_rejects_new_one():
    async def run():
        db, f = resource()
        await db.start()
        async with db.session():
            await db.close()
            with pytest.raises(StorageError, match="storage_closing"):
                async with db.session():
                    pass
        assert f.sessions[0].closed == f.sessions[0].rollbacks == 1
        assert f.engine.disposed == 1

    asyncio.run(run())


def test_session_factory_failure_disposes_initialized_engine():
    async def run():
        f = Factories()

        def broken(*args, **kwargs):
            raise RuntimeError("postgresql://secret-password@private")

        db = DatabaseResource(
            DatabaseSettings(enabled=True, url=URL),
            engine_factory=f.create_engine,
            session_factory_builder=broken,
        )
        with pytest.raises(StorageError) as caught:
            await db.start()
        assert "secret-password" not in str(caught.value)
        assert f.engine.disposed == 1

    asyncio.run(run())


def test_session_cleanup_failure_does_not_skip_engine_disposal(caplog):
    async def run():
        db, f = resource()
        await db.start()
        with pytest.raises(ValueError):
            async with db.session() as session:
                session.rollback_error = RuntimeError("secret-password")
                raise ValueError("business")
        assert f.sessions[0].closed == 1
        assert "secret-password" not in caplog.text
        await db.close()
        assert f.engine.disposed == 1

    asyncio.run(run())


def test_health_timeout_and_cancel_release_connection():
    async def run():
        db, f = resource(health_timeout=0.02)
        await db.start()
        f.engine.query_release = asyncio.Event()
        with pytest.raises(StorageError, match="storage_health_timeout"):
            await db.check_connection()
        assert f.engine.opened == f.engine.released == 1
        task = asyncio.create_task(db.check_connection())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert f.engine.opened == f.engine.released == 2
        await db.close()

    asyncio.run(run())


def test_dispose_failure_is_typed_and_has_no_raw_details():
    async def run():
        db, f = resource()
        await db.start()
        f.engine.dispose_error = RuntimeError("postgresql://secret-password@private")
        with pytest.raises(StorageError, match="storage_cleanup_failed") as caught:
            await db.close()
        assert "secret-password" not in str(caught.value)
        assert db.state == "closed"

    asyncio.run(run())


def test_cancel_during_close_waits_for_owned_session_cleanup():
    async def run():
        db, f = resource()
        await db.start()
        release = asyncio.Event()

        async def request():
            async with db.session() as session:
                session.close_release = release

        task = asyncio.create_task(request())
        while not f.sessions:
            await asyncio.sleep(0)
        await f.sessions[0].close_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await db.close()
        assert f.sessions[0].closed == 1 and f.engine.disposed == 1

    asyncio.run(run())


def test_session_cleanup_timeout_is_reported_and_engine_is_disposed(monkeypatch):
    import app.persistence.database as module

    monkeypatch.setattr(module, "CLEANUP_TIMEOUT_SECONDS", 0.02)

    async def run():
        db, f = resource()
        await db.start()
        with pytest.raises(StorageError, match="storage_cleanup_failed"):
            async with db.session() as session:
                session.close_release = asyncio.Event()
        await asyncio.sleep(0)
        try:
            await db.close()
        except StorageError as exc:
            assert exc.code == "storage_cleanup_failed"
        assert f.engine.disposed == 1

    asyncio.run(run())


def test_real_engine_and_factory_construction_is_lazy_without_connection():
    async def run():
        db = DatabaseResource(DatabaseSettings(enabled=True, url=URL))
        await db.start()
        assert db.engine.sync_engine.pool.checkedout() == 0
        async with db.session():
            assert db.engine.sync_engine.pool.checkedout() == 0
        await db.close()
        assert db.state == "closed"

    asyncio.run(run())


def test_raw_connection_error_maps_to_storage_unavailable():
    """asyncpg 的连接失败可能以原始 OSError 冒泡，也必须是 503 而不是 500。"""

    async def run():
        db, _f = resource()
        await db.start()
        codes = []
        try:
            for error in (
                ConnectionRefusedError(111, "refused"),
                OSError("network unreachable"),
            ):
                try:
                    async with db.session():
                        raise error
                except StorageError as exc:
                    codes.append((exc.code, exc.retryable))
        finally:
            await db.close()
        return codes

    assert asyncio.run(run()) == [
        ("storage_unavailable", False),
        ("storage_unavailable", False),
    ]


def test_close_marks_resource_closing_before_background_cleanup_runs():
    async def run():
        db, f = resource()
        await db.start()
        close_task = asyncio.create_task(db.close())
        await asyncio.sleep(0)
        with pytest.raises(StorageError, match="storage_closing"):
            async with db.session():
                pass
        await close_task
        assert f.sessions == [] and f.engine.disposed == 1

    asyncio.run(run())
