"""项目记忆Repository：短事务、有界锁、不可变内容版本及确定性错误。"""

import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.memory.errors import MemoryFailure
from app.memory.models import MemoryRecord, MemoryWriteResult, content_hash
from app.memory.retrieval import KEY_WEIGHT, KEYWORD_WEIGHT, TAG_WEIGHT, literal_pattern
from app.persistence.errors import StorageError
from app.persistence.memory_models import MEMORY_REVISION, AgentMemoryORM

LOCK_TIMEOUT_MS = 1000


def _scope(scope):
    return (
        AgentMemoryORM.scope_type == scope.scope_type,
        AgentMemoryORM.scope_id == scope.scope_id,
    )


def _sqlstate(exc):
    original = getattr(exc, "orig", exc)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _storage_failure(exc, *, committing=False):
    state = _sqlstate(exc)
    if state in {"42P01", "42703"}:
        return MemoryFailure("memory_schema_missing")
    if state in {"55P03", "40001", "40P01"}:
        return MemoryFailure("memory_busy")
    if isinstance(exc, IntegrityError):
        original = getattr(exc, "orig", exc)
        cause = getattr(original, "__cause__", original)
        constraint = getattr(cause, "constraint_name", None)
        if constraint in {
            "uq_agent_memory_active",
            "uq_agent_memory_version",
            "uq_agent_memory_successor",
        }:
            return MemoryFailure("memory_version_conflict")
        return MemoryFailure("memory_invalid_record")
    return MemoryFailure(
        "memory_commit_unknown" if committing else "memory_unavailable"
    )


class MemoryRepository:
    def __init__(self, resource):
        self.resource = resource

    async def check_schema(self):
        try:
            async with self.resource.session() as session:
                revision = await session.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                if revision != MEMORY_REVISION:
                    raise MemoryFailure("memory_schema_missing")
                await session.execute(select(AgentMemoryORM).limit(0))
        except MemoryFailure:
            raise
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc) from None

    @asynccontextmanager
    async def _write(self):
        committing = False
        try:
            async with self.resource.session() as session:
                try:
                    async with session.begin():
                        yield session
                        committing = True
                except (SQLAlchemyError, OSError) as exc:
                    # 在旧资源边界归一化驱动异常之前，保留本事务已知SQLSTATE。
                    raise _storage_failure(exc, committing=committing) from None
        except MemoryFailure:
            raise
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc, committing=committing) from None

    async def _lock(self, session, scope, key):
        # 仅数据库短事务内的逻辑键锁，不是Agent分布式调度锁；等待最多1秒。
        identity = f"{scope.scope_type}\0{scope.scope_id}\0{key}"
        lock_id = int.from_bytes(
            hashlib.blake2b(identity.encode(), digest_size=8).digest(),
            "big",
            signed=True,
        )
        await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:identity)"), {"identity": lock_id}
        )

    @staticmethod
    def _record(row):
        try:
            return MemoryRecord(
                **{key: getattr(row, key) for key in MemoryRecord.model_fields}
            )
        except (ValidationError, AttributeError):
            raise MemoryFailure("memory_invalid_record") from None

    @staticmethod
    def _row(scope, request, source, now, *, previous=None):
        return AgentMemoryORM(
            memory_id="mem_" + uuid4().hex,
            scope_type=scope.scope_type,
            scope_id=scope.scope_id,
            memory_type=request.memory_type,
            memory_key=request.memory_key,
            content=request.content,
            tags=list(request.tags),
            **source.model_dump(),
            status="active",
            version=previous.version + 1 if previous else 1,
            content_sha256=content_hash(request.content),
            created_at=now,
            updated_at=now,
            expires_at=request.expires_at,
            supersedes_id=previous.memory_id if previous else None,
        )

    async def create_memory(self, scope, request, source, *, now):
        async with self._write() as session:
            await self._lock(session, scope, request.memory_key)
            previous = await session.scalar(
                select(AgentMemoryORM)
                .where(*_scope(scope), AgentMemoryORM.memory_key == request.memory_key)
                .order_by(AgentMemoryORM.version.desc())
                .limit(1)
                .with_for_update()
            )
            if previous is not None and self._record(previous).active_at(now):
                if (
                    previous.content == request.content
                    and previous.memory_type == request.memory_type
                ):
                    result = MemoryWriteResult(
                        memory=self._record(previous), outcome="unchanged"
                    )
                else:
                    raise MemoryFailure("memory_conflict")
            else:
                if (
                    previous is not None
                    and previous.source_ref == source.source_ref
                    and previous.content == request.content
                    and previous.memory_type == request.memory_type
                ):
                    # 旧创建请求不能复活已删除/过期版本；需新的明确来源。
                    raise MemoryFailure("memory_version_conflict")
                if previous is not None and previous.status == "active":
                    previous.status, previous.updated_at = "expired", now
                    await session.flush()
                row = self._row(scope, request, source, now, previous=previous)
                session.add(row)
                await session.flush()
                result = MemoryWriteResult(memory=self._record(row), outcome="created")
        return result

    async def get_memory(self, scope, memory_id, *, now, include_inactive=False):
        try:
            async with self.resource.session() as session:
                row = await session.scalar(
                    select(AgentMemoryORM).where(
                        *_scope(scope), AgentMemoryORM.memory_id == memory_id
                    )
                )
                if row is None:
                    return None
                record = self._record(row)
                return record if include_inactive or record.active_at(now) else None
        except MemoryFailure:
            raise
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc) from None

    async def search_memories(self, scope, request, *, now):
        conditions = [
            *_scope(scope),
            AgentMemoryORM.status == "active",
            or_(AgentMemoryORM.expires_at.is_(None), AgentMemoryORM.expires_at > now),
        ]
        score = 0
        terms = []
        for keyword in request.keywords:
            pattern = literal_pattern(keyword)
            in_content = func.lower(AgentMemoryORM.content).like(pattern, escape="\\")
            in_key = AgentMemoryORM.memory_key.like(pattern, escape="\\")
            terms.append(or_(in_content, in_key))
            score += case((in_content, KEYWORD_WEIGHT), else_=0) + case(
                (in_key, KEY_WEIGHT), else_=0
            )
        if terms:
            conditions.append(or_(*terms))
        if request.tags:
            tags = [AgentMemoryORM.tags.contains([tag]) for tag in request.tags]
            conditions.append(or_(*tags))
            for match in tags:
                score += case((match, TAG_WEIGHT), else_=0)
        if request.memory_types:
            conditions.append(AgentMemoryORM.memory_type.in_(request.memory_types))
        order = [AgentMemoryORM.updated_at.desc(), AgentMemoryORM.memory_id.asc()]
        if not isinstance(score, int):
            order.insert(0, score.desc())
        statement = (
            select(AgentMemoryORM)
            .where(*conditions)
            .order_by(*order)
            .limit(request.top_k)
        )
        try:
            async with self.resource.session() as session:
                rows = (await session.scalars(statement)).all()
                return [self._record(row) for row in rows]
        except MemoryFailure:
            raise
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc) from None

    async def list_for_display(
        self, scope, *, query, memory_type, status, page, page_size, now
    ):
        # 展示用过滤和所有聚合均限定为宿主项目；有效期仅在读取时计算。
        effective = case(
            (
                AgentMemoryORM.status == "active",
                case((AgentMemoryORM.expires_at <= now, "expired"), else_="active"),
            ),
            else_=AgentMemoryORM.status,
        )
        conditions = list(_scope(scope))
        if status != "all":
            conditions.append(effective == status)
        if memory_type:
            conditions.append(AgentMemoryORM.memory_type == memory_type)
        if query:
            pattern = literal_pattern(query.lower())
            conditions.append(
                or_(
                    func.lower(AgentMemoryORM.content).like(pattern, escape="\\"),
                    func.lower(AgentMemoryORM.memory_key).like(pattern, escape="\\"),
                )
            )
        try:
            async with self.resource.session() as session:
                total = await session.scalar(
                    select(func.count()).select_from(AgentMemoryORM).where(*conditions)
                )
                rows = (
                    await session.scalars(
                        select(AgentMemoryORM)
                        .where(*conditions)
                        .order_by(
                            AgentMemoryORM.updated_at.desc(),
                            AgentMemoryORM.memory_id.asc(),
                        )
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                ).all()
                return [self._record(row) for row in rows], total
        except MemoryFailure:
            raise
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc) from None

    async def display_stats(self, scope, *, now):
        effective = case(
            (
                AgentMemoryORM.status == "active",
                case((AgentMemoryORM.expires_at <= now, "expired"), else_="active"),
            ),
            else_=AgentMemoryORM.status,
        )
        try:
            async with self.resource.session() as session:
                rows = (
                    await session.execute(
                        select(effective, func.count())
                        .where(*_scope(scope))
                        .group_by(effective)
                    )
                ).all()
                return dict(rows)
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc) from None

    async def display_successor(self, scope, memory_id):
        try:
            async with self.resource.session() as session:
                return await session.scalar(
                    select(AgentMemoryORM.memory_id).where(
                        *_scope(scope), AgentMemoryORM.supersedes_id == memory_id
                    )
                )
        except (StorageError, SQLAlchemyError, OSError) as exc:
            raise _storage_failure(exc) from None

    async def update_memory(self, scope, request, source, *, now):
        return await self.supersede_memory(scope, request, source, now=now)

    async def supersede_memory(self, scope, request, source, *, now):
        async with self._write() as session:
            await self._lock(session, scope, request.memory_key)
            row = await session.scalar(
                select(AgentMemoryORM)
                .where(*_scope(scope), AgentMemoryORM.memory_id == request.memory_id)
                .with_for_update()
            )
            if row is None:
                raise MemoryFailure("memory_not_found")
            if (
                row.memory_key != request.memory_key
                or row.version != request.expected_version
                or not self._record(row).active_at(now)
            ):
                raise MemoryFailure("memory_version_conflict")
            if (
                row.content == request.content
                and row.memory_type == request.memory_type
                and row.tags == request.tags
                and row.expires_at == request.expires_at
            ):
                result = MemoryWriteResult(
                    memory=self._record(row), outcome="unchanged"
                )
            else:
                row.status, row.updated_at = "superseded", now
                await session.flush()
                current = self._row(scope, request, source, now, previous=row)
                session.add(current)
                await session.flush()
                result = MemoryWriteResult(
                    memory=self._record(current), outcome="superseded"
                )
        return result

    async def soft_delete_memory(self, scope, request, source, *, now):
        async with self._write() as session:
            # 先只读取键，再按统一顺序获取逻辑键锁和行锁，避免相反锁序。
            key = await session.scalar(
                select(AgentMemoryORM.memory_key).where(
                    *_scope(scope), AgentMemoryORM.memory_id == request.memory_id
                )
            )
            if key is None:
                raise MemoryFailure("memory_not_found")
            await self._lock(session, scope, key)
            row = await session.scalar(
                select(AgentMemoryORM)
                .where(*_scope(scope), AgentMemoryORM.memory_id == request.memory_id)
                .with_for_update()
            )
            if row.version != request.expected_version or row.status not in {
                "active",
                "deleted",
            }:
                raise MemoryFailure("memory_version_conflict")
            if row.status == "deleted":
                result = MemoryWriteResult(
                    memory=self._record(row), outcome="unchanged"
                )
            else:
                row.status, row.updated_at = "deleted", now
                row.deleted_at, row.deleted_source_ref = now, source.source_ref
                await session.flush()
                result = MemoryWriteResult(memory=self._record(row), outcome="deleted")
        return result
