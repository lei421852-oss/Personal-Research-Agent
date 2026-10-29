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
