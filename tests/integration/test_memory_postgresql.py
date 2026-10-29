"""P2独立授权的真实PostgreSQL测试；只接受固定的一次性目标。"""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.memory.errors import MemoryFailure
from app.memory.models import (
    MemoryForget,
    MemorySave,
    MemoryScope,
    MemorySearch,
    MemoryUpdate,
)
from app.memory.policies import bind_source
from app.memory.retrieval import rank_records
from app.memory.service import MemoryService
from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.memory_models import AgentMemoryORM
from app.persistence.memory_repository import MemoryRepository

AUTHORIZED = os.environ.get("P2_TEST_MEMORY_AUTHORIZED") == "yes"
URL = os.environ.get("P2_TEST_MEMORY_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (AUTHORIZED and URL),
    reason="P2需要独立授权和固定隔离测试库，不能使用生产数据库。",
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
    assert valid, "P2数据库目标不是已授权的隔离环境"
    return DatabaseResource(DatabaseSettings(enabled=True, url=SecretStr(URL)))


def draft(content="Use formulas.", **changes):
    return MemorySave(
        memory_type="preference",
        memory_key="explanation.style",
        content=content,
        **changes,
    )


def scope():
    return MemoryScope(scope_id="p2_" + uuid4().hex)


def run(body):
    async def scenario():
        resource = checked_resource()
        await resource.start()
        try:
            repository = MemoryRepository(resource)
            await repository.check_schema()
            service = MemoryService(repository, scope(), clock=lambda: NOW)
            return await body(service, repository, resource)
        finally:
            await resource.close()

    return asyncio.run(scenario())


async def count(resource, scoped):
    async with resource.session() as session:
        return await session.scalar(
            select(func.count())
            .select_from(AgentMemoryORM)
            .where(AgentMemoryORM.scope_id == scoped.scope_id)
        )


def test_target_identity_is_real_authorized_database():
    async def body(service, repo, resource):
        async with resource.session() as session:
            row = (
                await session.execute(text("SELECT current_database(), current_user"))
            ).one()
        assert tuple(row) == ("personal_agent_memory_test", "p2_test")

    run(body)


def test_committed_memory_survives_new_engine_and_repository():
    async def body(service, repo, resource):
        original = (
            await service.save(draft(), question="Remember: Use formulas.")
        ).memory
        other = checked_resource()
        await other.start()
        try:
            later = await MemoryRepository(other).get_memory(
                service.scope, original.memory_id, now=NOW
            )
            assert later == original
        finally:
            await other.close()
        assert await count(resource, service.scope) == 1

    run(body)


def test_concurrent_identical_saves_have_one_active_record():
    async def body(service, repo, resource):
        values = await asyncio.gather(
            *[service.save(draft(), question="Use formulas.") for _ in range(8)]
        )
        assert len({v.memory.memory_id for v in values}) == 1
        assert sum(v.outcome == "created" for v in values) == 1
        assert await count(resource, service.scope) == 1

    run(body)


def test_conflicting_content_never_silently_overwrites():
    async def body(service, repo, resource):
        initial = (await service.save(draft(), question="Use formulas.")).memory
        with pytest.raises(MemoryFailure, match="memory_conflict"):
            await service.save(draft("Use code."), question="Use code.")
        later = await repo.get_memory(service.scope, initial.memory_id, now=NOW)
        assert later.content == "Use formulas." and later.version == 1
        assert await count(resource, service.scope) == 1

    run(body)


def test_update_retains_prior_version_and_source():
    async def body(service, repo, resource):
        initial = (
            await service.save(draft(), question="Remember: Use formulas.")
        ).memory
        request = MemoryUpdate(
            **draft("Use code.").model_dump(),
            memory_id=initial.memory_id,
            expected_version=1,
        )
        current = (
            await service.update(request, question="Change to: Use code.")
        ).memory
        prior = await repo.get_memory(
            service.scope, initial.memory_id, now=NOW, include_inactive=True
        )
        assert prior.status == "superseded" and prior.content == initial.content
        assert prior.source_ref == initial.source_ref
        assert current.supersedes_id == initial.memory_id and current.version == 2
        assert current.source_ref != prior.source_ref
        assert await count(resource, service.scope) == 2

    run(body)


def test_concurrent_updates_have_one_winner_and_explicit_stale_conflict():
    async def body(service, repo, resource):
        initial = (await service.save(draft(), question="Use formulas.")).memory
        requests = [
            MemoryUpdate(
                **draft(value).model_dump(),
                memory_id=initial.memory_id,
                expected_version=1,
            )
            for value in ["Use code.", "Use diagrams."]
        ]
        results = await asyncio.gather(
            *[service.update(r, question="Use code. Use diagrams.") for r in requests],
            return_exceptions=True,
        )
        assert sum(not isinstance(v, Exception) for v in results) == 1
        failures = [v for v in results if isinstance(v, MemoryFailure)]
        assert len(failures) == 1 and failures[0].code == "memory_version_conflict"
        assert await count(resource, service.scope) == 2

    run(body)


def test_soft_delete_is_idempotent_and_retains_audit():
    async def body(service, repo, resource):
        original = (await service.save(draft(), question="Use formulas.")).memory
        request = MemoryForget(
            memory_id=original.memory_id,
            expected_version=1,
            reason="Forget this preference.",
        )
        first = await service.forget(request, question="Forget this preference.")
        second = await service.forget(request, question="Forget this preference.")
        assert first.outcome == "deleted" and second.outcome == "unchanged"
        assert await service.search(MemorySearch(keywords=["formulas"])) == []
        assert await repo.get_memory(service.scope, original.memory_id, now=NOW) is None
        audit = await repo.get_memory(
            service.scope, original.memory_id, now=NOW, include_inactive=True
        )
        assert (
            audit.status == "deleted"
            and audit.content == original.content
            and audit.deleted_source_ref
        )

    run(body)


def test_expiry_and_recreation_keep_linear_history():
    async def body(service, repo, resource):
        original = (
            await service.save(
                draft(expires_at=NOW + timedelta(seconds=1)), question="Use formulas."
            )
        ).memory
        later = MemoryService(
            repo, service.scope, clock=lambda: NOW + timedelta(seconds=2)
        )
        assert await later.search(MemorySearch(keywords=["formulas"])) == []
        replacement = (
            await later.save(draft(), question="Please remember again: Use formulas.")
        ).memory
        assert (
            replacement.version == 2 and replacement.supersedes_id == original.memory_id
        )
        old = await repo.get_memory(
            service.scope, original.memory_id, now=NOW, include_inactive=True
        )
        assert old.status == "expired"

    run(body)


def test_project_scope_blocks_foreign_reads_and_writes():
    async def body(service, repo, resource):
        row = (await service.save(draft(), question="Use formulas.")).memory
        other = MemoryService(repo, scope(), clock=lambda: NOW)
        assert await other.search(MemorySearch(memory_types=["preference"])) == []
        assert (
            await repo.get_memory(
                other.scope, row.memory_id, now=NOW, include_inactive=True
            )
            is None
        )
        request = MemoryUpdate(
            **draft("Use code.").model_dump(),
            memory_id=row.memory_id,
            expected_version=1,
        )
        with pytest.raises(MemoryFailure, match="memory_not_found"):
            await other.update(request, question="Use code.")

    run(body)


def test_chinese_literal_keywords_tags_ranking_and_top_k():
    async def body(service, repo, resource):
        rows = []
        for index in range(12):
            request = MemorySave(
                memory_type="project_fact",
                memory_key=f"facts.item{index}",
                content=f"公式检索条目{index}",
                tags=["数学"] if index % 2 else ["代码"],
            )
            rows.append((await service.save(request, question=request.content)).memory)
        query = MemorySearch(keywords=["公式"], tags=["数学"], top_k=5)
        assert await service.search(query) == rank_records(rows, query, NOW)
        assert (
            len(
                await service.search(
                    MemorySearch(memory_types=["project_fact"], top_k=10)
                )
            )
            == 10
        )

    run(body)


def test_like_wildcards_are_literals_and_values_are_parameterized():
    async def body(service, repo, resource):
        for index, value in enumerate(["Use 100% examples.", "Use plain examples."]):
            request = MemorySave(
                memory_type="workflow_lesson",
                memory_key=f"examples.item{index}",
                content=value,
            )
            await service.save(request, question=value)
        result = await service.search(MemorySearch(keywords=["%"]))
        assert len(result) == 1 and "100%" in result[0].content
        assert await service.search(MemorySearch(keywords=["' OR 1=1 --"])) == []

    run(body)


def test_mid_transaction_failure_rolls_back_supersession(monkeypatch):
    async def body(service, repo, resource):
        row = (await service.save(draft(), question="Use formulas.")).memory
        original = repo._row

        def invalid_row(*args, **kwargs):
            record = original(*args, **kwargs)
            record.source_ref = "invalid-test-reference"
            return record

        monkeypatch.setattr(repo, "_row", invalid_row)
        update = MemoryUpdate(
            **draft("Use code.").model_dump(),
            memory_id=row.memory_id,
            expected_version=1,
        )
        with pytest.raises(MemoryFailure, match="memory_invalid_record"):
            await service.update(update, question="Use code.")
        old = await repo.get_memory(service.scope, row.memory_id, now=NOW)
        assert old.status == "active" and old.content == "Use formulas."
        assert await count(resource, service.scope) == 1

    run(body)


class LostAcknowledgement:
    def __init__(self, inner):
        self.inner = inner

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        value = await self.inner.__aexit__(*args)
        if args[0] is None:
            raise OperationalError(
                "controlled lost commit acknowledgement", None, Exception("fixture")
            )
        return value


class SessionProxy:
    def __init__(self, inner):
        self.inner = inner

    def begin(self):
        return LostAcknowledgement(self.inner.begin())

    def __getattr__(self, name):
        return getattr(self.inner, name)


class ResourceProxy:
    def __init__(self, inner):
        self.inner = inner

    @asynccontextmanager
    async def session(self):
        async with self.inner.session() as session:
            yield SessionProxy(session)


def test_committed_but_lost_ack_is_unknown_not_rollback_or_empty():
    async def body(service, repo, resource):
        request = draft()
        uncertain = MemoryRepository(ResourceProxy(resource))
        with pytest.raises(MemoryFailure, match="memory_commit_unknown"):
            await uncertain.create_memory(
                service.scope,
                request,
                bind_source("Use formulas.", request.content),
                now=NOW,
            )
        assert len(await service.search(MemorySearch(keywords=["formulas"]))) == 1
        assert await count(resource, service.scope) == 1

    run(body)


def test_lock_wait_is_bounded_and_reported_as_busy():
    async def body(service, repo, resource):
        async with resource.session() as session, session.begin():
            await repo._lock(session, service.scope, "explanation.style")
            with pytest.raises(MemoryFailure, match="memory_busy"):
                await service.save(draft(), question="Use formulas.")
        assert await count(resource, service.scope) == 0

    run(body)


def test_tampered_content_is_not_returned_as_valid_memory():
    async def body(service, repo, resource):
        row = (await service.save(draft(), question="Use formulas.")).memory
        async with resource.session() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE agent_memories SET content=:value WHERE memory_id=:identity"
                ),
                {"value": "tampered fixture", "identity": row.memory_id},
            )
        with pytest.raises(MemoryFailure, match="memory_invalid_record"):
            await service.search(MemorySearch(keywords=["tampered"]))

    run(body)


def test_real_repository_through_scripted_react_tools():
    async def body(service, repo, resource):
        from app.research.react_runtime import ReActWorkbenchRuntime
        from app.research.run_manager import MainAgentRunRequest
        from tests.test_agent_memory import SAVE, memory_finish
        from tests.test_react_workbench import (
            CATALOG,
            Dependencies,
            PaperSearch,
            factory,
        )

        deps = Dependencies()
        deps.environment = dict
        llm, vision, _, _, _ = factory(deps, [("memory_save", SAVE), memory_finish])
        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=llm,
            vision_factory=vision,
            paper_retriever=PaperSearch(),
            paper_catalog=CATALOG,
            memory_service=service,
        )
        result = await runtime.run(
            MainAgentRunRequest(
                question="Remember: Prefer formulas.", max_model_calls=200
            ),
            lambda **_: None,
        )
        assert result["status"] == "completed", result
        assert len(await service.search(MemorySearch(keywords=["formulas"]))) == 1
        assert result["answer_first"]["citations"] == []

    run(body)


def test_cancelled_waiting_writer_releases_transaction_and_pool(monkeypatch):
    async def body(service, repo, resource):
        entered = asyncio.Event()
        original = repo._lock
        async with resource.session() as session, session.begin():
            await original(session, service.scope, "explanation.style")

            async def waiting(*args):
                entered.set()
                return await original(*args)

            monkeypatch.setattr(repo, "_lock", waiting)
            task = asyncio.create_task(service.save(draft(), question="Use formulas."))
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        assert await count(resource, service.scope) == 0
        result = await service.save(draft(), question="Use formulas.")
        assert result.outcome == "created"

    run(body)


def test_old_creation_source_cannot_reactivate_a_deleted_version():
    async def body(service, repo, resource):
        row = (await service.save(draft(), question="Use formulas.")).memory
        await service.forget(
            MemoryForget(
                memory_id=row.memory_id,
                expected_version=1,
                reason="Forget this preference.",
            ),
            question="Forget this preference.",
        )
        with pytest.raises(MemoryFailure, match="memory_version_conflict"):
            await service.save(draft(), question="Use formulas.")
        assert await count(resource, service.scope) == 1
        assert await service.search(MemorySearch(keywords=["formulas"])) == []

    run(body)


def test_old_source_cannot_reset_expired_memory_ttl():
    async def body(service, repo, resource):
        await service.save(
            draft(expires_at=NOW + timedelta(seconds=1)), question="Use formulas."
        )
        later = MemoryService(
            repo, service.scope, clock=lambda: NOW + timedelta(seconds=2)
        )
        with pytest.raises(MemoryFailure, match="memory_version_conflict"):
            await later.save(draft(), question="Use formulas.")
        assert await count(resource, service.scope) == 1
        assert await later.search(MemorySearch(keywords=["formulas"])) == []

    run(body)
