"""H2.2 阶段 2.1：三张业务表、命名约束与部分唯一索引。

Revision ID: h2_0001
Revises:
Create Date: 2026-10-25

本迁移是历史快照：枚举值与约束条件在此固化，不导入 app.persistence.models，
以免后续模型改动反向改写已发布的迁移。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h2_0001"
down_revision = None
branch_labels = None
depends_on = None

_RUN_STATUSES = "'queued','running','completed','failed','cancelled'"
_ACTIVE_STATUSES = "'queued','running'"
_TERMINAL_STATUSES = "'completed','failed','cancelled'"
_NODE_TYPES = (
    "'Planner','Worker','Validator','Reviewer','Recovery','Synthesis',"
    "'QueryLanguage','PaperSelection','ReportLanguage','MainAgent',"
    "'PaperSearch','QueryExpansion','Evidence','ResearchTool'"
)


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("run_id", sa.CHAR(32), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'queued'")
        ),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("request_hash", sa.CHAR(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.CHAR(64), nullable=True),
        sa.Column("fingerprint", sa.CHAR(64), nullable=True),
        sa.Column(
            "effective_paper_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("owner_instance_id", sa.CHAR(32), nullable=False),
        sa.Column(
            "state_version", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "last_event_seq", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("current_report_revision", sa.Integer(), nullable=True),
        sa.Column(
            "public_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "event_limit_reached",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("finalize_token", sa.CHAR(32), nullable=True),
        sa.Column("finalize_sha256", sa.CHAR(64), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_research_runs"),
        sa.CheckConstraint("mode IN ('live','replay')", name="ck_research_runs_mode"),
        sa.CheckConstraint(
            f"status IN ({_RUN_STATUSES})", name="ck_research_runs_status"
        ),
        sa.CheckConstraint("state_version >= 0", name="ck_research_runs_state_version"),
        sa.CheckConstraint(
            "last_event_seq >= 0", name="ck_research_runs_last_event_seq"
        ),
        sa.CheckConstraint(
            "current_report_revision IS NULL OR current_report_revision > 0",
            name="ck_research_runs_report_revision",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'"
            " AND (fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$')"
            " AND (idempotency_key_hash IS NULL"
            " OR idempotency_key_hash ~ '^[0-9a-f]{64}$')"
            " AND (finalize_sha256 IS NULL OR finalize_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_research_runs_hash_format",
        ),
        sa.CheckConstraint(
            f"(status IN ({_ACTIVE_STATUSES})"
            " AND finished_at IS NULL"
            " AND error IS NULL"
            " AND finalize_token IS NULL"
            " AND finalize_sha256 IS NULL)"
            " OR "
            f"(status IN ({_TERMINAL_STATUSES})"
            " AND finished_at IS NOT NULL"
            " AND finalize_token IS NOT NULL"
            " AND finalize_sha256 IS NOT NULL)",
            name="ck_research_runs_terminal_consistency",
        ),
    )

    op.create_table(
        "research_reports",
        sa.Column("run_id", sa.CHAR(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revision_token", sa.CHAR(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_kind", sa.Text(), nullable=False),
        sa.Column("public_payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.CHAR(64), nullable=False),
        sa.Column("authority_payload", postgresql.JSONB(), nullable=True),
        sa.Column("source_reference", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "revision", name="pk_research_reports"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.run_id"],
            name="fk_research_reports_run",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "run_id", "revision_token", name="uq_research_reports_token"
        ),
        sa.CheckConstraint("revision > 0", name="ck_research_reports_revision"),
        sa.CheckConstraint(
            "snapshot_kind IN ('answer_first','execution_report')",
            name="ck_research_reports_kind",
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_research_reports_schema"),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_research_reports_hash"
        ),
    )

    # 循环引用：指针外键在两张表都建好之后添加，并延迟到提交时校验。
    op.create_foreign_key(
        "fk_research_runs_current_report",
        "research_runs",
        "research_reports",
        ["run_id", "current_report_revision"],
        ["run_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "research_events",
        sa.Column("run_id", sa.CHAR(32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.CHAR(64), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_research_events"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.run_id"],
            name="fk_research_events_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("seq > 0", name="ck_research_events_seq"),
        sa.CheckConstraint(
            f"node_type IN ({_NODE_TYPES})", name="ck_research_events_node_type"
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','skipped')",
            name="ck_research_events_status",
        ),
        sa.CheckConstraint(
            "event_type IN ('started','progress','completed','failed','skipped')",
            name="ck_research_events_event_type",
        ),
        sa.CheckConstraint(
            "source IN ('live','derived','replay')", name="ck_research_events_source"
        ),
        sa.CheckConstraint("length(summary) > 0", name="ck_research_events_summary"),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_research_events_hash"
        ),
    )

    op.create_index(
        "uq_research_runs_active_fingerprint",
        "research_runs",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text(
            "mode = 'live' AND status IN ('queued', 'running')"
            " AND fingerprint IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_research_runs_idempotency_key",
        "research_runs",
        ["idempotency_key_hash"],
        unique=True,
        postgresql_where=sa.text("idempotency_key_hash IS NOT NULL"),
    )
    op.create_index("ix_research_runs_created_at", "research_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_research_runs_created_at", table_name="research_runs")
    op.drop_index("uq_research_runs_idempotency_key", table_name="research_runs")
    op.drop_index("uq_research_runs_active_fingerprint", table_name="research_runs")
    op.drop_table("research_events")
    op.drop_constraint(
        "fk_research_runs_current_report", "research_runs", type_="foreignkey"
    )
    op.drop_table("research_reports")
    op.drop_table("research_runs")
