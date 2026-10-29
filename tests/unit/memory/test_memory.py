"""记忆契约、来源、生命周期和工具边界的确定性离线测试。"""

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.memory.config import MemorySettings
from app.memory.errors import MemoryFailure
from app.memory.models import (
    MemoryForget,
    MemorySave,
    MemoryScope,
    MemorySearch,
    MemoryUpdate,
)
from app.memory.retrieval import literal_pattern, rank_records
from app.memory.service import MemoryService
from app.memory.tools import MemoryBindings
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from tests.unit.memory.fakes import FakeMemoryRepository, MemoryStore

NOW = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
SCOPE = MemoryScope(scope_id="test_project")


def run(coro):
    return asyncio.run(coro)


def invoke(operation):
    return run(operation())


def draft(content="优先使用公式解释", **changes):
    return MemorySave(
        memory_type="preference",
        memory_key="preference.explain",
        content=content,
        **changes,
    )


def service(store=None, scope=SCOPE, clock=None):
    return MemoryService(
        FakeMemoryRepository(store), scope, clock=clock or (lambda: NOW)
    )


def test_disabled_imports_no_database_packages():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import app.workbench, app.memory.config; import sys; assert 'sqlalchemy' not in sys.modules; assert 'asyncpg' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_configuration_explicit_project_and_safe_errors():
    assert MemorySettings.from_environment({}).enabled is False
    assert (
        MemorySettings.from_environment(
            {"AGENT_MEMORY_ENABLED": "true", "AGENT_MEMORY_PROJECT_ID": "project_a"}
        ).project_id
        == "project_a"
    )
    for values in [
        {"AGENT_MEMORY_ENABLED": "invalid_secret"},
        {"AGENT_MEMORY_ENABLED": "true"},
        {
            "AGENT_MEMORY_ENABLED": "true",
            "AGENT_MEMORY_PROJECT_ID": "postgresql://secret",
        },
    ]:
        with pytest.raises(MemoryFailure) as error:
            MemorySettings.from_environment(values)
        assert error.value.code == "memory_configuration_invalid"
        assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_type": "paper_evidence"},
        {"memory_key": "Wrong Key"},
        {"content": ""},
        {"content": "a\u0000b"},
        {"scope_id": "other"},
        {"tags": [""]},
        {"tags": ["a"] * 9},
        {"expires_at": "2026-10-29"},
    ],
)
def test_invalid_save_contract(changes):
    values = {
        "memory_type": "preference",
        "memory_key": "preference.explain",
        "content": "formula",
    } | changes
    with pytest.raises(ValidationError):
        MemorySave.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"keywords": [""]},
        {"keywords": ["a"], "top_k": 0},
        {"keywords": ["a"], "top_k": 11},
        {"keywords": ["a"], "top_k": True},
        {"keywords": ["a"], "scope_id": "other"},
    ],
)
def test_invalid_search_contract(changes):
    with pytest.raises(ValidationError):
        MemorySearch.model_validate(changes)


@pytest.mark.parametrize(
    "content",
    [
        "password=not-a-real-password",
        "sk-fakefixtureabcdefghijk",
        "postgresql://u:pw@localhost/other",
        "phone 13800138000",
        "contact fixture@example.invalid",
        "id 110101199001011234",
    ],
)
def test_sensitive_content_is_rejected_before_repository(content):
    svc = service()
    with pytest.raises(MemoryFailure) as error:
        run(svc.save(draft(content), question=content))
    assert error.value.code == "memory_sensitive_content"
    assert svc.repository.store.calls == []


def test_invented_source_and_expired_new_save_are_rejected():
    svc = service()
    with pytest.raises(MemoryFailure, match="memory_source_rejected"):
        run(svc.save(draft("模型猜测"), question="用户没有表达这个偏好"))
    with pytest.raises(MemoryFailure, match="memory_invalid_arguments"):
        run(svc.save(draft(expires_at=NOW), question="优先使用公式解释"))


def test_idempotency_and_conflict_do_not_overwrite_original_source():
    svc = service()
    first = run(svc.save(draft(), question="请记住：优先使用公式解释"))
    second = run(svc.save(draft(), question="以后优先使用公式解释"))
    assert second.outcome == "unchanged" and first.memory == second.memory
    assert len(svc.repository.store.records) == 1
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        run(svc.save(draft("优先使用代码解释"), question="优先使用代码解释"))
    assert (
        svc.repository.store.records[first.memory.memory_id].content
        == "优先使用公式解释"
    )


def test_update_retains_immutable_prior_content_and_stale_update_fails():
    svc = service()
    original = run(svc.save(draft(), question="优先使用公式解释")).memory
    update = MemoryUpdate(
        **draft("优先使用代码解释").model_dump(),
        memory_id=original.memory_id,
        expected_version=1,
    )
    current = run(svc.update(update, question="改为优先使用代码解释")).memory
    assert current.version == 2 and current.supersedes_id == original.memory_id
    prior = svc.repository.store.records[original.memory_id]
    assert prior.status == "superseded" and prior.content == original.content
    with pytest.raises(MemoryFailure, match="memory_version_conflict"):
        run(svc.update(update, question="优先使用代码解释"))
    assert len(svc.repository.store.records) == 2


def test_delete_expiry_and_new_repository_read_are_distinct():
    store = MemoryStore()
    clock = [NOW]
    svc = service(store, clock=lambda: clock[0])
    saved = run(
        svc.save(
            draft(expires_at=NOW + timedelta(seconds=5)), question="优先使用公式解释"
        )
    ).memory
    fresh = service(store, clock=lambda: clock[0])
    assert (
        run(fresh.search(MemorySearch(keywords=["公式"])))[0].memory_id
        == saved.memory_id
    )
    clock[0] += timedelta(seconds=6)
    assert run(fresh.search(MemorySearch(keywords=["公式"]))) == []
    new = run(fresh.save(draft(), question="请重新记录：优先使用公式解释")).memory
    assert new.version == 2 and store.records[saved.memory_id].status == "expired"
    forgotten = run(
        fresh.forget(
            MemoryForget(memory_id=new.memory_id, expected_version=2, reason="请忘记"),
            question="请忘记",
        )
    ).memory
    assert forgotten.status == "deleted" and forgotten.deleted_source_ref
    assert run(fresh.search(MemorySearch(keywords=["公式"]))) == []
    assert len(store.records) == 2


def test_scope_isolation_for_reads_and_mutations():
    store = MemoryStore()
    a, b = service(store), service(store, MemoryScope(scope_id="other"))
    row = run(a.save(draft(), question="优先使用公式解释")).memory
    assert run(b.search(MemorySearch(memory_types=["preference"]))) == []
    update = MemoryUpdate(
        **draft().model_dump(), memory_id=row.memory_id, expected_version=1
    )
    with pytest.raises(MemoryFailure, match="memory_not_found"):
        run(b.update(update, question="优先使用公式解释"))


def test_literal_keyword_tag_ranking_and_top_k():
    svc = service()
    for i in range(12):
        value = MemorySave(
            memory_type="project_fact",
            memory_key=f"facts.item{i}",
            content=f"公式检索条目{i}",
            tags=["数学"] if i % 2 else ["代码"],
        )
        run(svc.save(value, question=value.content))
    req = MemorySearch(keywords=["公式"], tags=["数学"], top_k=5)
    result = run(svc.search(req))
    assert len(result) == 5 and all("数学" in r.tags for r in result)
    assert result == rank_records(list(svc.repository.store.records.values()), req, NOW)
    assert literal_pattern("a%b_c\\d") == "%a\\%b\\_c\\\\d%"


def test_database_failure_does_not_become_empty():
    svc = service()
    svc.repository.store.failure = "memory_unavailable"
    bindings = MemoryBindings(svc, "优先使用公式解释", invoke)
    registry = ToolRegistry()
    bindings.register(registry)
    result = ToolExecutor(registry).execute("memory_search", {"keywords": ["公式"]})
    assert result.status == "error" and result.issue.code == "dependency_failure"
    assert result.payload["memory_error_code"] == "memory_unavailable"


def test_tools_refuse_unknown_scope_and_unobserved_update_without_repository_calls():
    svc = service()
    bindings = MemoryBindings(svc, "优先使用公式解释", invoke)
    registry = ToolRegistry()
    bindings.register(registry)
    executor = ToolExecutor(registry)
    result = executor.execute(
        "memory_search", {"keywords": ["公式"], "scope_id": "other"}
    )
    assert result.issue.code == "invalid_arguments"
    update = draft().model_dump() | {
        "memory_id": "mem_" + "0" * 32,
        "expected_version": 1,
    }
    result = executor.execute("memory_update", update)
    assert result.payload["memory_error_code"] == "memory_scope_forbidden"
    assert svc.repository.store.calls == []


def test_unknown_commit_is_latched_no_second_write_and_read_can_reconcile():
    svc = service()
    svc.repository.store.failure = "memory_commit_unknown"
    bindings = MemoryBindings(svc, "优先使用公式解释", invoke)
    registry = ToolRegistry()
    bindings.register(registry)
    executor = ToolExecutor(registry)
    args = draft().model_dump(mode="json")
    first = executor.execute("memory_save", args)
    second = executor.execute("memory_save", args)
    assert (
        first.payload["retry_mode"] == second.payload["retry_mode"] == "reconcile_only"
    )
    assert svc.repository.store.calls.count("create") == 1
    assert len(svc.repository.store.records) == 1
    svc.repository.store.failure = None
    found = executor.execute("memory_search", {"keywords": ["公式"]})
    assert found.status == "success" and bindings.pending_writes
    assert found.result_count == 0 and "evidence" not in found.payload


def test_record_tampering_is_not_accepted():
    from app.memory.models import MemoryRecord

    svc = service()
    row = run(svc.save(draft(), question="优先使用公式解释")).memory
    with pytest.raises(ValidationError):
        MemoryRecord.model_validate(row.model_dump() | {"content": "不同内容"})


def test_service_rejects_wrong_scope_returned_by_a_faulty_backend():
    a = service()
    row = run(a.save(draft(), question="优先使用公式解释")).memory

    async def wrong_scope(*_args, **_kwargs):
        return [row.model_copy(update={"scope_id": "foreign"})]

    a.repository.search_memories = wrong_scope
    with pytest.raises(MemoryFailure, match="memory_scope_forbidden"):
        run(a.search(MemorySearch(keywords=["公式"])))


def test_tags_are_deduplicated_without_silent_content_rewrite():
    value = draft(tags=["Math", "math", " 数学 "])
    assert value.tags == ["math", "数学"]
    assert value.content == "优先使用公式解释"


def test_workbench_disabled_memory_keeps_replay_startup_database_free():
    code = """from fastapi.testclient import TestClient
from app.workbench import create_app
from app.persistence.config import DatabaseSettings
from app.memory.config import MemorySettings
import sys
app = create_app(database_settings=DatabaseSettings(), memory_settings=MemorySettings())
with TestClient(app) as client:
    assert client.get('/').status_code == 200
    assert app.state.memory_service is None
    assert 'sqlalchemy' not in sys.modules
    assert 'asyncpg' not in sys.modules
assert app.state.database.state == 'closed'
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_invalid_enabled_memory_configuration_closes_database_resource():
    from fastapi.testclient import TestClient

    from app.persistence.config import DatabaseSettings
    from app.workbench import create_app

    app = create_app(
        database_settings=DatabaseSettings(),
        memory_settings=MemorySettings(enabled=True, project_id="test_project"),
    )
    with (
        pytest.raises(MemoryFailure, match="memory_configuration_invalid"),
        TestClient(app),
    ):
        pass
    assert app.state.database.state == "closed"


def test_replayed_creation_cannot_revive_forgotten_memory():
    value = service()
    row = run(value.save(draft(), question="优先使用公式解释")).memory
    run(
        value.forget(
            MemoryForget(memory_id=row.memory_id, expected_version=1, reason="请忘记"),
            question="请忘记",
        )
    )
    with pytest.raises(MemoryFailure, match="memory_version_conflict"):
        run(value.save(draft(), question="优先使用公式解释"))
    assert len(value.repository.store.records) == 1
    assert next(iter(value.repository.store.records.values())).status == "deleted"
