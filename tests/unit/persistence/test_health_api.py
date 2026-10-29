import asyncio
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.dependencies import get_db_session
from app.persistence.errors import DatabaseConfigurationError
from app.workbench import create_app
from tests.unit.persistence.fakes import Factories, FakeManager

URL = "postgresql+asyncpg://reader:fixture-password@localhost/personal_agent_test_unit?ssl=disable"


def make_app(**settings):
    f = Factories()
    db = DatabaseResource(
        DatabaseSettings(enabled=True, url=URL, **settings),
        engine_factory=f.create_engine,
        session_factory_builder=f.make_sessions,
    )
    app = create_app(
        manager_factory=FakeManager,
        database_settings=db.settings,
        database_factory=lambda _: db,
    )
    return app, db, f


def test_health_is_explicit_read_only_connection_check():
    app, _db, f = make_app()
    with TestClient(app) as client:
        assert f.engine.opened == 0
        result = client.get("/api/storage/health")
        assert result.status_code == 200
        assert result.json() == {"status": "ready", "scope": "connection_only"}
        assert f.engine.statements == ["SELECT 1"]
        assert f.engine.released == 1


@pytest.mark.parametrize(
    "error_kind,code",
    [
        ("connect", "storage_unavailable"),
        ("query", "storage_unavailable"),
        ("pool", "storage_pool_timeout"),
        ("timeout", "storage_health_timeout"),
    ],
)
def test_health_errors_are_safe(error_kind, code, caplog):
    app, _db, f = make_app(health_timeout=0.01)
    secret = "secret-password-at-private-host"
    if error_kind == "pool":
        from sqlalchemy.exc import TimeoutError

        f.engine.connect_error = TimeoutError(secret)
    elif error_kind == "connect":
        f.engine.connect_error = RuntimeError(secret)
    elif error_kind == "query":
        f.engine.execute_error = RuntimeError(secret)
    else:
        f.engine.query_release = asyncio.Event()
    with TestClient(app) as client:
        response = client.get("/api/storage/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == code
        assert secret not in response.text + caplog.text
        assert "Traceback" not in response.text


def test_session_dependency_yields_distinct_sessions_and_closes_them():
    app, _db, f = make_app()

    @app.get("/test/session")
    async def consume(session: Annotated[object, Depends(get_db_session)]):
        return {"created": session is not None}

    with TestClient(app) as client:
        assert client.get("/test/session").json() == {"created": True}
        assert client.get("/test/session").status_code == 200
    assert len(f.sessions) == 2
    assert all(s.rollbacks == s.closed == 1 for s in f.sessions)


def test_bad_startup_configuration_has_no_secret(monkeypatch):
    monkeypatch.setenv("H2_DATABASE_ENABLED", "true")
    monkeypatch.setenv("H2_DATABASE_URL", "postgresql://fixture-secret@private-host")
    app = create_app(manager_factory=FakeManager)
    with pytest.raises(DatabaseConfigurationError) as caught, TestClient(app):
        pass
    assert "fixture-secret" not in str(caught.value)
    assert "private-host" not in str(caught.value)


def test_session_driver_error_uses_safe_error_envelope(caplog):
    from sqlalchemy.exc import DBAPIError

    app, _db, f = make_app()

    @app.get("/test/session-error")
    async def consume(session: Annotated[object, Depends(get_db_session)]):
        raise DBAPIError(
            "SELECT fixture-secret",
            {"password": "fixture-secret"},
            RuntimeError("fixture-secret"),
        )

    with TestClient(app) as client:
        response = client.get("/test/session-error")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        assert "fixture-secret" not in response.text + caplog.text
    assert f.sessions[0].closed == f.sessions[0].rollbacks == 1
