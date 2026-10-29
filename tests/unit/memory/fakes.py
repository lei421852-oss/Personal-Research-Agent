"""只供离线测试的领域夹具；不能作为PostgreSQL事务证明。"""

from uuid import uuid4

from app.memory.errors import MemoryFailure
from app.memory.models import MemoryRecord, MemoryWriteResult, content_hash
from app.memory.retrieval import rank_records


class MemoryStore:
    def __init__(self):
        self.records = {}
        self.calls = []
        self.failure = None


class FakeMemoryRepository:
    def __init__(self, store=None):
        self.store = store or MemoryStore()

    def _rows(self, scope):
        return [
            r
            for r in self.store.records.values()
            if (r.scope_type, r.scope_id) == (scope.scope_type, scope.scope_id)
        ]

    def _check(self, operation):
        self.store.calls.append(operation)
        if self.store.failure and self.store.failure != "memory_commit_unknown":
            raise MemoryFailure(self.store.failure)

    def _put(self, scope, request, source, now, previous=None):
        row = MemoryRecord(
            memory_id="mem_" + uuid4().hex,
            **scope.model_dump(),
            memory_type=request.memory_type,
            memory_key=request.memory_key,
            content=request.content,
            tags=request.tags,
            **source.model_dump(),
            status="active",
            version=previous.version + 1 if previous else 1,
            content_sha256=content_hash(request.content),
            created_at=now,
            updated_at=now,
            expires_at=request.expires_at,
            supersedes_id=previous.memory_id if previous else None,
        )
        self.store.records[row.memory_id] = row
        return row

    async def create_memory(self, scope, request, source, *, now):
        self._check("create")
        rows = [r for r in self._rows(scope) if r.memory_key == request.memory_key]
        previous = max(rows, key=lambda r: r.version) if rows else None
        if previous and previous.active_at(now):
            if (
                previous.content != request.content
                or previous.memory_type != request.memory_type
            ):
                raise MemoryFailure("memory_conflict")
            return MemoryWriteResult(memory=previous, outcome="unchanged")
        if (
            previous
            and previous.source_ref == source.source_ref
            and previous.content == request.content
            and previous.memory_type == request.memory_type
        ):
            raise MemoryFailure("memory_version_conflict")
        if previous and previous.status == "active":
            self.store.records[previous.memory_id] = previous.model_copy(
                update={"status": "expired", "updated_at": now}
            )
        row = self._put(scope, request, source, now, previous)
        if self.store.failure == "memory_commit_unknown":
            raise MemoryFailure("memory_commit_unknown")
        return MemoryWriteResult(memory=row, outcome="created")

    async def get_memory(self, scope, memory_id, *, now, include_inactive=False):
        self._check("get")
        row = next((r for r in self._rows(scope) if r.memory_id == memory_id), None)
        return row if row and (include_inactive or row.active_at(now)) else None

    async def search_memories(self, scope, request, *, now):
        self._check("search")
        return rank_records(self._rows(scope), request, now)

    async def update_memory(self, scope, request, source, *, now):
        self._check("update")
        previous = self.store.records.get(request.memory_id)
        if previous is None or previous not in self._rows(scope):
            raise MemoryFailure("memory_not_found")
        if (
            previous.version != request.expected_version
            or previous.memory_key != request.memory_key
            or not previous.active_at(now)
        ):
            raise MemoryFailure("memory_version_conflict")
        if (
            previous.content,
            previous.tags,
            previous.memory_type,
            previous.expires_at,
        ) == (request.content, request.tags, request.memory_type, request.expires_at):
            return MemoryWriteResult(memory=previous, outcome="unchanged")
        self.store.records[previous.memory_id] = previous.model_copy(
            update={"status": "superseded", "updated_at": now}
        )
        row = self._put(scope, request, source, now, previous)
        return MemoryWriteResult(memory=row, outcome="superseded")

    async def soft_delete_memory(self, scope, request, source, *, now):
        self._check("forget")
        row = self.store.records.get(request.memory_id)
        if row is None or row not in self._rows(scope):
            raise MemoryFailure("memory_not_found")
        if row.version != request.expected_version or row.status not in {
            "active",
            "deleted",
        }:
            raise MemoryFailure("memory_version_conflict")
        if row.status == "deleted":
            return MemoryWriteResult(memory=row, outcome="unchanged")
        row = row.model_copy(
            update={
                "status": "deleted",
                "updated_at": now,
                "deleted_at": now,
                "deleted_source_ref": source.source_ref,
            }
        )
        self.store.records[row.memory_id] = row
        return MemoryWriteResult(memory=row, outcome="deleted")
