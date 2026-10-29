"""仅在维护者明确授权独立临时PostgreSQL后运行；不建库、不建表、不迁移。"""

import asyncio
import logging
import os
import secrets

import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.errors import StorageError


@pytest.fixture
def authorized_settings():
    if os.environ.get("H2_TEST_DATABASE_AUTHORIZED") != "yes":
        pytest.skip("Temporary PostgreSQL target not explicitly authorized")
    raw = os.environ.get("H2_TEST_DATABASE_URL")
    if not raw:
        pytest.fail("Authorized temporary target URL missing", pytrace=False)
    settings = DatabaseSettings(
        enabled=True,
        url=raw,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.05,
        command_timeout=0.1,
    )
    parsed, _ = settings.connection_options()
    if parsed.host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    } or not parsed.database.startswith("personal_agent_test_"):
        pytest.fail(
            "Only confirmed local disposable test targets are accepted", pytrace=False
        )
    runtime = os.environ.get("H2_DATABASE_URL")
    if runtime:
        from sqlalchemy.engine import make_url

        try:
            other = make_url(runtime)
        except Exception:  # noqa: BLE001 - 测试目标不明确时直接拒绝连接
            pytest.fail("Runtime target cannot be safely distinguished", pytrace=False)
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if (
            other.host in local_hosts
            and other.database == parsed.database
            and (other.port or 5432) == parsed.port
        ):
            pytest.fail("Test target must differ from runtime target", pytrace=False)
    return settings


def test_postgresql_session_rollback_pool_timeout_and_cleanup(authorized_settings):
    async def run():
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError, TimeoutError

        db = DatabaseResource(authorized_settings)
        await db.start()
        try:
            await db.check_connection()
            async with db.session() as session:
                assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
                # 核对服务端实际身份，而非只相信连接串中的目标名称。
                identity = (
                    await session.execute(
                        text("SELECT current_database(), current_user")
                    )
                ).one()
                target, _ = authorized_settings.connection_options()
                assert tuple(identity) == (target.database, target.username)
                with pytest.raises(DBAPIError):
                    await session.execute(text("SELECT 1/0"))
                await session.rollback()
                assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            assert not session.in_transaction()
            assert db.engine.sync_engine.pool.checkedout() == 0
            async with db.engine.connect():
                with pytest.raises(TimeoutError):
                    async with db.engine.connect():
                        pass
            await db.check_connection()
            with pytest.raises(
                (DBAPIError, TimeoutError, asyncio.TimeoutError, StorageError)
            ):
                async with db.session() as session:
                    await session.execute(text("SELECT pg_sleep(1)"))
            await db.check_connection()
            async with db.engine.connect() as connection:
                await connection.invalidate()
            await db.check_connection()
            assert db.engine.sync_engine.pool.checkedout() == 0
        finally:
            await db.close()
        assert db.state == "closed"

    asyncio.run(run())


def test_postgresql_authentication_failure_is_redacted(authorized_settings, caplog):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from sqlalchemy import event
    from sqlalchemy.engine import make_url

    from app.workbench import create_app
    from tests.unit.persistence.fakes import FakeManager

    target, _ = authorized_settings.connection_options()
    wrong_password = secrets.token_urlsafe(32)
    while wrong_password == target.password:
        wrong_password = secrets.token_urlsafe(32)
    # 保留维护者授权的TLS选择，错误认证只改变密码。
    raw_target = authorized_settings.url.get_secret_value()
    wrong_url = make_url(raw_target).set(password=wrong_password)
    raw_wrong_url = wrong_url.render_as_string(hide_password=False)
    settings = authorized_settings.model_copy(update={"url": SecretStr(raw_wrong_url)})
    app = create_app(manager_factory=FakeManager, database_settings=settings)
    failures = []

    def record_authentication_error(context):
        original = context.original_exception
        failures.append(getattr(original, "sqlstate", None))

    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        db = app.state.database
        event.listen(db.engine.sync_engine, "handle_error", record_authentication_error)
        response = client.get("/api/storage/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        # PostgreSQL的28P01证明故障来自密码认证，而非普通连接失败。
        assert "28P01" in failures
        assert db.engine.sync_engine.pool.checkedout() == 0
    assert db.state == "closed"
    public_output = response.text + caplog.text + repr(settings) + repr(db)
    forbidden = (target.password, wrong_password, raw_target, raw_wrong_url)
    if any(value and value in public_output for value in forbidden):
        pytest.fail(
            "Database authentication diagnostics leaked a secret", pytrace=False
        )
    assert "Traceback" not in response.text


def test_postgresql_cancelled_session_releases_connection(authorized_settings):
    async def run():
        from sqlalchemy import event, text

        db = DatabaseResource(
            authorized_settings.model_copy(update={"command_timeout": 5.0})
        )
        await db.start()
        query_started = asyncio.Event()

        def before_execute(_connection, _cursor, statement, *_args):
            if statement == "SELECT pg_sleep(1)":
                query_started.set()

        event.listen(db.engine.sync_engine, "before_cursor_execute", before_execute)

        async def consume_session():
            async with db.session() as session:
                await session.execute(text("SELECT 1"))
                await session.execute(text("SELECT pg_sleep(1)"))

        task = None
        try:
            task = asyncio.create_task(consume_session())
            await asyncio.wait_for(query_started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert db.engine.sync_engine.pool.checkedout() == 0
            assert not db._leases
            await db.check_connection()
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await db.close()
        assert db.state == "closed"

    asyncio.run(run())
