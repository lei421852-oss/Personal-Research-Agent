import asyncio
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.workbench import create_app
from tests.unit.persistence.fakes import Factories, FakeManager

URL = "postgresql+asyncpg://reader:fixture-password@localhost/personal_agent_test_unit?ssl=disable"


def app_with_resource(manager_factory):
    factories = Factories()
    db = DatabaseResource(
        DatabaseSettings(enabled=True, url=URL),
        engine_factory=factories.create_engine,
        session_factory_builder=factories.make_sessions,
    )
    return (
        create_app(
            manager_factory=manager_factory,
            database_settings=db.settings,
            database_factory=lambda _: db,
        ),
        db,
        factories,
    )


def test_disabled_subprocess_blocks_database_imports_and_preserves_replay():
    script = """
import importlib.abc, sys, os
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'sqlalchemy','asyncpg'}:
            raise AssertionError('database import attempted while disabled')
sys.meta_path.insert(0, Block())
os.environ['H2_DATABASE_ENABLED']='false'
from fastapi.testclient import TestClient
from app.workbench import create_app
from tests.test_research_run_manager import make_manager, FakeDependencies
with TestClient(create_app(manager_factory=lambda: make_manager(dependencies=FakeDependencies(False)))) as c:
    assert c.get('/api/storage/health').json()['status']=='disabled'
    r=c.post('/api/research/replays/g2')
    assert r.status_code==201
    assert c.get('/api/research/runs/'+r.json()['run_id']+'/report').status_code==200
assert not any(x in sys.modules for x in ('sqlalchemy','asyncpg'))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_manager_start_failure_disposes_database():
    def broken():
        raise ValueError("manager failed")

    app, db, f = app_with_resource(broken)
    with pytest.raises(ValueError, match="manager failed"), TestClient(app):
        pass
    assert db.state == "closed" and f.engine.disposed == 1


def test_manager_close_error_does_not_skip_database_disposal():
    manager = FakeManager()
    manager.close_error = ValueError("manager close failed")
    app, db, f = app_with_resource(lambda: manager)
    with pytest.raises(ValueError, match="manager close failed"), TestClient(app):
        pass
    assert manager.closed == 1 and f.engine.disposed == 1 and db.state == "closed"


def test_different_lifespans_create_independent_engines():
    factories = []

    def make_db(settings):
        f = Factories()
        factories.append(f)
        return DatabaseResource(
            settings,
            engine_factory=f.create_engine,
            session_factory_builder=f.make_sessions,
        )

    app = create_app(
        manager_factory=FakeManager,
        database_settings=DatabaseSettings(enabled=True, url=URL),
        database_factory=make_db,
    )
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/api/storage/health").status_code == 200
    assert len(factories) == 2
    assert factories[0].engine is not factories[1].engine
    assert all(f.engine.disposed == 1 for f in factories)


def test_cancelled_lifespan_still_closes_manager_and_database():
    async def run():
        manager = FakeManager()
        app, _db, f = app_with_resource(lambda: manager)
        entered = asyncio.Event()

        async def serve():
            async with app.router.lifespan_context(app):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(serve())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.closed == f.engine.disposed == 1

    asyncio.run(run())
