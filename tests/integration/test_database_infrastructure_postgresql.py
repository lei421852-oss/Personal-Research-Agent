"""仅在维护者明确授权独立临时PostgreSQL后运行；不建库、不建表、不迁移。"""

import asyncio
import os

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
                with pytest.raises(DBAPIError):
                    await session.execute(text("SELECT 1/0"))
                await session.rollback()
                assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
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
