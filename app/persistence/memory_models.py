"""独立记忆ORM元数据，复用原数据库；不污染原三表Base。"""

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MEMORY_REVISION = "p2_0001"


class MemoryBase(DeclarativeBase):
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})


class AgentMemoryORM(MemoryBase):
    __tablename__ = "agent_memories"

    memory_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    memory_key: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True))
    supersedes_id: Mapped[str | None] = mapped_column(CHAR(36))
    deleted_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True))
    deleted_source_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "memory_key",
            "version",
            name="uq_agent_memory_version",
        ),
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "memory_key",
            "memory_id",
            name="uq_agent_memory_identity",
        ),
        UniqueConstraint("supersedes_id", name="uq_agent_memory_successor"),
        ForeignKeyConstraint(
            ["scope_type", "scope_id", "memory_key", "supersedes_id"],
            [
                "agent_memories.scope_type",
                "agent_memories.scope_id",
                "agent_memories.memory_key",
                "agent_memories.memory_id",
            ],
            name="fk_agent_memory_previous",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope_type = 'project' AND length(scope_id) BETWEEN 1 AND 64",
            name="ck_agent_memory_scope",
        ),
        CheckConstraint(
            "memory_type IN ('preference','project_decision','project_fact','workflow_lesson','research_context')",
            name="ck_agent_memory_type",
        ),
        CheckConstraint(
            "status IN ('active','superseded','deleted','expired')",
            name="ck_agent_memory_status",
        ),
        CheckConstraint(
            "memory_id ~ '^mem_[0-9a-f]{32}$' AND content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_memory_hash_id",
        ),
        CheckConstraint(
            "memory_key ~ '^[a-z][a-z0-9_.:-]*$' AND length(memory_key) <= 80",
            name="ck_agent_memory_key",
        ),
        CheckConstraint(
            "length(content) BETWEEN 1 AND 2000 AND length(source_excerpt) BETWEEN 1 AND 2000",
            name="ck_agent_memory_content",
        ),
        CheckConstraint(
            "jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 8",
            name="ck_agent_memory_tags",
        ),
        CheckConstraint(
            "source_type = 'user_statement' AND source_ref ~ '^question_sha256:[0-9a-f]{64}$'",
            name="ck_agent_memory_source",
        ),
        CheckConstraint(
            "(version = 1 AND supersedes_id IS NULL) OR (version > 1 AND supersedes_id IS NOT NULL)",
            name="ck_agent_memory_chain",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (expires_at IS NULL OR expires_at > created_at)",
            name="ck_agent_memory_times",
        ),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL AND deleted_source_ref IS NOT NULL) OR (status <> 'deleted' AND deleted_at IS NULL AND deleted_source_ref IS NULL)",
            name="ck_agent_memory_deleted",
        ),
        Index(
            "uq_agent_memory_active",
            "scope_type",
            "scope_id",
            "memory_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_agent_memory_scope_updated",
            "scope_type",
            "scope_id",
            "status",
            "updated_at",
        ),
    )
