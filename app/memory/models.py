"""记忆领域契约，不依赖ORM、工具或LLM。"""

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

MemoryType = Literal[
    "preference",
    "project_decision",
    "project_fact",
    "workflow_lesson",
    "research_context",
]
MemoryStatus = Literal["active", "superseded", "deleted", "expired"]
MemoryID = Annotated[StrictStr, Field(pattern=r"^mem_[0-9a-f]{32}$")]
Key = Annotated[
    StrictStr, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.:-]*$")
]
Text = Annotated[StrictStr, Field(min_length=1, max_length=2000)]


class MemoryModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class MemoryScope(MemoryModel):
    scope_type: Literal["project"] = "project"
    scope_id: Annotated[
        StrictStr,
        Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$"),
    ]


def aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("invalid_memory_timestamp") from None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("memory_timestamp_requires_timezone")
    return value.astimezone(UTC)


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MemorySource(MemoryModel):
    source_type: Literal["user_statement"] = "user_statement"
    source_ref: Annotated[StrictStr, Field(pattern=r"^question_sha256:[0-9a-f]{64}$")]
    source_excerpt: Text


class MemorySave(MemoryModel):
    memory_type: MemoryType
    memory_key: Key
    content: Text
    tags: list[Annotated[StrictStr, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=8
    )
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def trim_content(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("invalid_memory_content")
        return value

    @field_validator("tags")
    @classmethod
    def canonical_tags(cls, values):
        result = sorted({v.strip().lower() for v in values})
        if any(not value or any(ord(c) < 32 for c in value) for value in result):
            raise ValueError("empty_memory_tag")
        return result

    @field_validator("expires_at", mode="before")
    @classmethod
    def aware_expiry(cls, value):
        return aware(value)


class MemoryUpdate(MemorySave):
    memory_id: MemoryID
    expected_version: Annotated[StrictInt, Field(ge=1)]


class MemoryForget(MemoryModel):
    memory_id: MemoryID
    expected_version: Annotated[StrictInt, Field(ge=1)]
    reason: Text


class MemorySearch(MemoryModel):
    keywords: list[Annotated[StrictStr, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=8
    )
    tags: list[Annotated[StrictStr, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=8
    )
    memory_types: list[MemoryType] = Field(default_factory=list, max_length=5)
    top_k: Annotated[StrictInt, Field(ge=1, le=10)] = 5

    @field_validator("keywords", "tags")
    @classmethod
    def canonical_terms(cls, values):
        result = sorted({v.strip().lower() for v in values})
        if any(not value or any(ord(c) < 32 for c in value) for value in result):
            raise ValueError("empty_memory_term")
        return result

    @model_validator(mode="after")
    def require_filter(self):
        if not (self.keywords or self.tags or self.memory_types):
            raise ValueError("memory_search_requires_filter")
        return self


class MemoryRecord(MemoryModel):
    memory_id: MemoryID
    scope_type: Literal["project"]
    scope_id: StrictStr
    memory_type: MemoryType
    memory_key: Key
    content: Text
    tags: list[StrictStr]
    source_type: Literal["user_statement"]
    source_ref: StrictStr
    source_excerpt: Text
    status: MemoryStatus
    version: Annotated[StrictInt, Field(ge=1)]
    content_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    supersedes_id: MemoryID | None = None
    deleted_at: datetime | None = None
    deleted_source_ref: StrictStr | None = None

    @field_validator(
        "created_at", "updated_at", "expires_at", "deleted_at", mode="before"
    )
    @classmethod
    def timestamps(cls, value):
        return aware(value)

    @model_validator(mode="after")
    def integrity(self):
        if content_hash(self.content) != self.content_sha256:
            raise ValueError("memory_content_hash_mismatch")
        return self

    def active_at(self, now):
        return self.status == "active" and (
            self.expires_at is None or self.expires_at > now
        )


class MemoryWriteResult(MemoryModel):
    memory: MemoryRecord
    outcome: Literal["created", "unchanged", "superseded", "deleted"]
