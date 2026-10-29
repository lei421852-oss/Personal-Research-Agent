"""记忆 API 离线契约；Fake 数据不代替 PostgreSQL 证据。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memories import router
from app.memory.errors import MemoryFailure
from app.memory.models import MemoryRecord, MemoryScope, content_hash
from app.memory.service import MemoryService

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def row(number, *, scope="project_a", status="active", expired=False, prior=None):
    text = f"数学记忆 {number}"
    return MemoryRecord(
        memory_id="mem_" + f"{number:032x}",
        scope_type="project",
        scope_id=scope,
        memory_type="preference",
        memory_key=f"pref.item{number}",
        content=text,
        tags=["数学"],
        source_type="user_statement",
        source_ref="question_sha256:" + "a" * 64,
        source_excerpt=text,
        status=status,
        version=1,
        content_sha256=content_hash(text),
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(seconds=number),
        expires_at=NOW - timedelta(days=1) if expired else None,
        supersedes_id=prior,
        deleted_at=NOW if status == "deleted" else None,
        deleted_source_ref="question_sha256:" + "b" * 64
        if status == "deleted"
        else None,
    )


def effective(record, now):
    return (
        "expired"
        if record.status == "active" and not record.active_at(now)
        else record.status
    )


class FakeDisplayRepository:
    def __init__(self, records):
        self.records = records
        self.failure = False

    def rows(self, scope):
        if self.failure:
            raise MemoryFailure("memory_unavailable")
        return [
            r
            for r in self.records
            if (r.scope_type, r.scope_id) == (scope.scope_type, scope.scope_id)
        ]

    async def list_for_display(
        self, scope, *, query, memory_type, status, page, page_size, now
    ):
        rows = [
            r
            for r in self.rows(scope)
            if (status == "all" or effective(r, now) == status)
            and (not memory_type or r.memory_type == memory_type)
            and (
                not query
                or query.lower() in r.content.lower()
                or query.lower() in r.memory_key.lower()
            )
        ]
        rows.sort(key=lambda r: r.memory_id)
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        offset = (page - 1) * page_size
        return rows[offset : offset + page_size], len(rows)

    async def display_stats(self, scope, *, now):
        counts = {}
        for r in self.rows(scope):
            status = effective(r, now)
            counts[status] = counts.get(status, 0) + 1
        return counts

    async def get_memory(self, scope, memory_id, *, now, include_inactive=False):
        assert include_inactive
        return next((r for r in self.rows(scope) if r.memory_id == memory_id), None)

    async def display_successor(self, scope, memory_id):
        return next(
            (r.memory_id for r in self.rows(scope) if r.supersedes_id == memory_id),
            None,
        )


@pytest.fixture
def client():
    records = [row(i) for i in range(1, 27)]
    records += [
        row(27, status="superseded"),
        row(28, expired=True),
        row(29, status="deleted"),
        row(30, prior=row(27).memory_id),
        row(31, scope="another_project"),
    ]
    repository = FakeDisplayRepository(records)
    app = FastAPI()
    app.include_router(router)
    app.state.memory_service = MemoryService(
        repository, MemoryScope(scope_id="project_a"), clock=lambda: NOW
    )
    with TestClient(app) as client:
        yield client, repository, records


def test_defaults_pagination_filters_and_scope(client):
    api, _, _ = client
    first = api.get("/api/memories").json()
    second = api.get("/api/memories", params={"page": 2}).json()
    assert first["total"] == 27 and len(first["items"]) == 20
    assert len(second["items"]) == 7
    ids1 = {r["memory_id"] for r in first["items"]}
    ids2 = {r["memory_id"] for r in second["items"]}
    assert not ids1 & ids2
    assert all(r["scope_id"] == "project_a" for r in first["items"] + second["items"])
    found = api.get(
        "/api/memories", params={"query": "记忆 5", "memory_type": "preference"}
    ).json()
    assert [r["content"] for r in found["items"]] == ["数学记忆 5"]
    assert api.get("/api/memories", params={"status": "all"}).json()["total"] == 30


def test_stats_uses_effective_expiration_and_detail_successor(client):
    api, _, records = client
    assert api.get("/api/memories/stats").json() == {
        "total": 30,
        "active": 27,
        "superseded": 1,
        "expired": 1,
        "deleted": 1,
    }
    expired = api.get("/api/memories", params={"status": "expired"}).json()["items"]
    assert len(expired) == 1 and expired[0]["status"] == "active"
    assert expired[0]["effective_status"] == "expired"
    old = api.get("/api/memories/" + records[26].memory_id).json()
    assert old["superseded_by_id"] == records[29].memory_id
    assert old["source_ref"].startswith("question_sha256:")


def test_cross_project_detail_and_invalid_identity_are_not_found(client):
    api, _, records = client
    assert api.get("/api/memories/" + records[-1].memory_id).status_code == 404
    assert api.get("/api/memories/not-an-id").status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"scope_id": "another_project"},
        {"unexpected": "x"},
        {"status": "unknown"},
        {"memory_type": "unknown"},
        {"page": 0},
        {"page_size": 101},
        {"page": "invalid"},
        {"query": "x" * 201},
    ],
)
def test_invalid_parameters_are_rejected_without_reflection(client, params):
    api, _, _ = client
    response = api.get("/api/memories", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_arguments"
    assert "another_project" not in response.text


def test_duplicate_and_scope_parameters_rejected_everywhere(client):
    api, _, records = client
    assert api.get("/api/memories?page=1&page=2").status_code == 422
    assert api.get("/api/memories/stats?scope_id=another_project").status_code == 422
    assert (
        api.get(
            "/api/memories/" + records[0].memory_id + "?scope_id=another_project"
        ).status_code
        == 422
    )


def test_unavailable_not_confused_with_empty(client):
    api, repo, _ = client
    repo.failure = True
    for path in [
        "/api/memories",
        "/api/memories/stats",
        "/api/memories/" + "mem_" + "0" * 31 + "1",
    ]:
        response = api.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "memory_unavailable"
        assert "postgres" not in response.text
    repo.failure = False
    result = api.get("/api/memories", params={"query": "not present"})
    assert result.status_code == 200 and result.json()["items"] == []


def test_disabled_memory_returns_503():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as api:
        assert api.get("/api/memories").status_code == 503
        assert api.get("/api/memories/stats").status_code == 503


def test_service_rejects_backend_returning_foreign_scope(client):
    api, repo, records = client

    async def wrong_scope(*args, **kwargs):
        return [records[-1]], 1

    repo.list_for_display = wrong_scope
    assert api.get("/api/memories").status_code == 503
