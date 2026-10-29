"""只允许固定授权隔离库的记忆展示 API 集成测试。"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.api.memories import router
from app.memory.models import MemoryForget, MemorySave, MemoryScope, MemoryUpdate
from app.memory.service import MemoryService
from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.memory_repository import MemoryRepository

AUTHORIZED = os.environ.get("P2_TEST_MEMORY_AUTHORIZED") == "yes"
URL = os.environ.get("P2_TEST_MEMORY_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (AUTHORIZED and URL),
    reason="需要授权的独立 P2 临时测试库。",
)
NOW = datetime(2026, 10, 29, tzinfo=UTC)


def checked_resource():
    try:
        value = make_url(URL)
        valid = (
            value.drivername == "postgresql+asyncpg"
            and value.host == "127.0.0.1"
            and value.port == 55437
            and value.database == "personal_agent_memory_test"
            and value.username == "p2_test"
            and value.query == {"ssl": "disable"}
            and bool(value.password)
        )
    except (ValueError, TypeError):
        valid = False
    assert valid, "目标不是获授权的隔离测试数据库"
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


async def make_fixture():
    resource = checked_resource()
    await resource.start()
    repository = MemoryRepository(resource)
    await repository.check_schema()
    scope = MemoryScope(scope_id="ui_" + uuid4().hex)
    writer = MemoryService(repository, scope, clock=lambda: NOW)
    view = MemoryService(repository, scope, clock=lambda: NOW + timedelta(seconds=2))

    def draft(key, text, **kwargs):
        return MemorySave(
            memory_type="preference", memory_key=key, content=text, **kwargs
        )

    initial = (
        await writer.save(draft("display.style", "公式优先"), question="公式优先")
    ).memory
    current = (
        await writer.update(
            MemoryUpdate(
                **draft("display.style", "代码优先").model_dump(),
                memory_id=initial.memory_id,
                expected_version=initial.version,
            ),
            question="代码优先",
        )
    ).memory
    second = (
        await writer.save(draft("display.second", "讲解案例"), question="讲解案例")
    ).memory
    expired = (
        await writer.save(
            draft("display.expire", "临时偏好", expires_at=NOW + timedelta(seconds=1)),
            question="临时偏好",
        )
    ).memory
    deleted = (
        await writer.save(draft("display.delete", "过时偏好"), question="过时偏好")
    ).memory
    await writer.forget(
        MemoryForget(
            memory_id=deleted.memory_id,
            expected_version=deleted.version,
            reason="忘记过时偏好",
        ),
        question="忘记过时偏好",
    )
    foreign_service = MemoryService(
        repository, MemoryScope(scope_id="other_" + uuid4().hex), clock=lambda: NOW
    )
    foreign = (
        await foreign_service.save(
            draft("display.foreign", "其他项目内容"), question="其他项目内容"
        )
    ).memory
    app = FastAPI()
    app.state.memory_service = view
    app.include_router(router)
    return resource, app, initial, current, second, expired, deleted, foreign


def test_real_database_list_pagination_filters_and_statistics():
    async def scenario():
        (
            resource,
            app,
            initial,
            current,
            second,
            expired,
            deleted,
            foreign,
        ) = await make_fixture()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                stats = await client.get("/api/memories/stats")
                assert stats.status_code == 200
                assert stats.json() == {
                    "total": 5,
                    "active": 2,
                    "superseded": 1,
                    "expired": 1,
                    "deleted": 1,
                }
                first = await client.get("/api/memories", params={"page_size": 1})
                next_page = await client.get(
                    "/api/memories", params={"page": 2, "page_size": 1}
                )
                assert first.status_code == next_page.status_code == 200
                first_body, next_body = first.json(), next_page.json()
                assert first_body["total"] == next_body["total"] == 2
                assert {
                    first_body["items"][0]["memory_id"],
                    next_body["items"][0]["memory_id"],
                } == {current.memory_id, second.memory_id}
                matches = await client.get(
                    "/api/memories",
                    params={"query": "代码", "memory_type": "preference"},
                )
                assert [row["memory_id"] for row in matches.json()["items"]] == [
                    current.memory_id
                ]
                literal = await client.get("/api/memories", params={"query": "%"})
                assert literal.json()["total"] == 0
                all_rows = await client.get("/api/memories", params={"status": "all"})
                ids = {row["memory_id"] for row in all_rows.json()["items"]}
                assert ids == {
                    initial.memory_id,
                    current.memory_id,
                    second.memory_id,
                    expired.memory_id,
                    deleted.memory_id,
                }
                assert foreign.memory_id not in ids
                old = await client.get("/api/memories", params={"status": "expired"})
                assert old.json()["items"][0]["memory_id"] == expired.memory_id
                assert old.json()["items"][0]["effective_status"] == "expired"
        finally:
            await resource.close()

    asyncio.run(scenario())


def test_real_database_detail_is_scoped_and_preserves_provenance():
    async def scenario():
        resource, app, initial, current, _, _, _, foreign = await make_fixture()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                detail = await client.get("/api/memories/" + initial.memory_id)
                assert detail.status_code == 200
                body = detail.json()
                assert body["source_ref"] == initial.source_ref
                assert body["effective_status"] == "superseded"
                assert body["superseded_by_id"] == current.memory_id
                assert (
                    await client.get("/api/memories/" + foreign.memory_id)
                ).status_code == 404
                assert (
                    await client.get(
                        "/api/memories", params={"scope_id": foreign.scope_id}
                    )
                ).status_code == 422
        finally:
            await resource.close()

    asyncio.run(scenario())


def test_real_database_failure_is_not_an_empty_list():
    async def scenario():
        resource, app, *_ = await make_fixture()
        await resource.close()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.get("/api/memories")
            assert result.status_code == 503
            assert result.json()["error"]["code"] == "memory_unavailable"
            assert URL not in result.text

    asyncio.run(scenario())
