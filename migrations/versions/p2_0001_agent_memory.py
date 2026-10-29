"""P2项目记忆表；仅新增表，不改变h2_0001历史或原三表。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p2_0001"
down_revision = "h2_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_memories",
        sa.Column("memory_id", sa.CHAR(36), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("memory_key", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(64), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("supersedes_id", sa.CHAR(36)),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("deleted_source_ref", sa.Text()),
        sa.PrimaryKeyConstraint("memory_id", name="pk_agent_memories"),
        sa.UniqueConstraint(
            "scope_type",
            "scope_id",
            "memory_key",
            "version",
            name="uq_agent_memory_version",
        ),
        sa.UniqueConstraint(
            "scope_type",
            "scope_id",
            "memory_key",
            "memory_id",
            name="uq_agent_memory_identity",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_agent_memory_successor"),
        sa.ForeignKeyConstraint(
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
        sa.CheckConstraint(
            "scope_type = 'project' AND length(scope_id) BETWEEN 1 AND 64",
            name="ck_agent_memory_scope",
        ),
        sa.CheckConstraint(
            "memory_type IN ('preference','project_decision','project_fact','workflow_lesson','research_context')",
            name="ck_agent_memory_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','superseded','deleted','expired')",
            name="ck_agent_memory_status",
        ),
        sa.CheckConstraint(
            "memory_id ~ '^mem_[0-9a-f]{32}$' AND content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_memory_hash_id",
        ),
        sa.CheckConstraint(
            "memory_key ~ '^[a-z][a-z0-9_.:-]*$' AND length(memory_key) <= 80",
            name="ck_agent_memory_key",
        ),
        sa.CheckConstraint(
            "length(content) BETWEEN 1 AND 2000 AND length(source_excerpt) BETWEEN 1 AND 2000",
            name="ck_agent_memory_content",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 8",
            name="ck_agent_memory_tags",
        ),
        sa.CheckConstraint(
            "source_type = 'user_statement' AND source_ref ~ '^question_sha256:[0-9a-f]{64}$'",
            name="ck_agent_memory_source",
        ),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_id IS NULL) OR (version > 1 AND supersedes_id IS NOT NULL)",
            name="ck_agent_memory_chain",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND (expires_at IS NULL OR expires_at > created_at)",
            name="ck_agent_memory_times",
        ),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL AND deleted_source_ref IS NOT NULL) OR (status <> 'deleted' AND deleted_at IS NULL AND deleted_source_ref IS NULL)",
            name="ck_agent_memory_deleted",
        ),
    )
    op.create_index(
        "uq_agent_memory_active",
        "agent_memories",
        ["scope_type", "scope_id", "memory_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_agent_memory_scope_updated",
        "agent_memories",
        ["scope_type", "scope_id", "status", "updated_at"],
    )


def downgrade():
    op.drop_index("ix_agent_memory_scope_updated", table_name="agent_memories")
    op.drop_index("uq_agent_memory_active", table_name="agent_memories")
    op.drop_table("agent_memories")
