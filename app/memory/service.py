"""业务规则与来源约束；Repository不决定哪些信息值得记忆。"""

from datetime import UTC, datetime

from app.memory.errors import MemoryFailure
from app.memory.policies import bind_source, validate_safe


class MemoryService:
    def __init__(self, repository, scope, *, clock=None):
        self.repository, self.scope = repository, scope
        self.clock = clock or (lambda: datetime.now(UTC))

    async def search(self, request):
        records = await self.repository.search_memories(
            self.scope, request, now=self.clock()
        )
        for record in records:
            self._check_scope(record)
        return records

    async def save(self, request, *, question):
        now = self.clock()
        self._validate(request, now)
        result = await self.repository.create_memory(
            self.scope, request, bind_source(question, request.content), now=now
        )
        self._check_scope(result.memory)
        return result

    async def update(self, request, *, question):
        now = self.clock()
        self._validate(request, now)
        result = await self.repository.update_memory(
            self.scope, request, bind_source(question, request.content), now=now
        )
        self._check_scope(result.memory)
        return result

    async def forget(self, request, *, question):
        result = await self.repository.soft_delete_memory(
            self.scope, request, bind_source(question, request.reason), now=self.clock()
        )
        self._check_scope(result.memory)
        return result

    async def list_for_display(self, *, query, memory_type, status, page, page_size):
        now = self.clock()
        records, total = await self.repository.list_for_display(
            self.scope,
            query=query,
            memory_type=memory_type,
            status=status,
            page=page,
            page_size=page_size,
            now=now,
        )
        for record in records:
            self._check_scope(record)
        return [self._display(record, now) for record in records], total

    async def display_stats(self):
        counts = await self.repository.display_stats(self.scope, now=self.clock())
        result = {
            name: counts.get(name, 0)
            for name in ("active", "superseded", "expired", "deleted")
        }
        return {"total": sum(result.values()), **result}

    async def display_detail(self, memory_id):
        now = self.clock()
        record = await self.repository.get_memory(
            self.scope, memory_id, now=now, include_inactive=True
        )
        if record is None:
            return None
        self._check_scope(record)
        result = self._display(record, now)
        result["superseded_by_id"] = await self.repository.display_successor(
            self.scope, memory_id
        )
        return result

    @staticmethod
    def _display(record, now):
        result = record.model_dump(mode="json")
        result["effective_status"] = (
            "expired"
            if record.status == "active" and not record.active_at(now)
            else record.status
        )
        return result

    def _check_scope(self, record):
        if (record.scope_type, record.scope_id) != (
            self.scope.scope_type,
            self.scope.scope_id,
        ):
            raise MemoryFailure("memory_scope_forbidden")

    @staticmethod
    def _validate(request, now):
        validate_safe(request.content, request.memory_key, *request.tags)
        if request.expires_at is not None and request.expires_at <= now:
            raise MemoryFailure("memory_invalid_arguments")
