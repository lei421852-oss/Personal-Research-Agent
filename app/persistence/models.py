"""H2.2 三张业务表的 ORM 定义；应用启动不建表、不执行迁移。

仅在持久化启用时由 Repository / 迁移环境导入；`app.workbench` 不导入本模块，
因此默认禁用的库不会把 SQLAlchemy 拉进运行时。
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
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

# 迁移环境与运行时版本检查共用的标识；改表时必须同步改这两处。
SCHEMA_VERSION = 1
EXPECTED_ALEMBIC_REVISION = "h2_0001"

RUN_MODES = ("live", "replay")
RUN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
ACTIVE_RUN_STATUSES = ("queued", "running")
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")
NODE_TYPES = (
    "Planner",
    "Worker",
    "Validator",
    "Reviewer",
    "Recovery",
    "Synthesis",
    "QueryLanguage",
    "PaperSelection",
    "ReportLanguage",
    "MainAgent",
    "PaperSearch",
    "QueryExpansion",
    "Evidence",
    "ResearchTool",
)
NODE_STATUSES = ("pending", "running", "completed", "failed", "skipped")
EVENT_TYPES = ("started", "progress", "completed", "failed", "skipped")
EVENT_SOURCES = ("live", "derived", "replay")
SNAPSHOT_KINDS = ("answer_first", "execution_report")


def _in_clause(values) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _hash_check(column: str) -> str:
    return f"{column} ~ '^[0-9a-f]{{64}}$'"


class Base(DeclarativeBase):
    # 只约定主键名，其余约束名一律显式书写，便于与迁移逐字对照。
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})


class ResearchRunORM(Base):
    """一次研究的生命周期、请求身份与终态幂等身份。"""

    __tablename__ = "research_runs"

    run_id: Mapped[str] = mapped_column(CHAR(32), primary_key=True)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    idempotency_key_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    effective_paper_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    owner_instance_id: Mapped[str] = mapped_column(CHAR(32), nullable=False)
    state_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    current_report_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    public_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    event_limit_reached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    finalize_token: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    finalize_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    started_at: Mapped[object | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    finished_at: Mapped[object | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"mode IN {_in_clause(RUN_MODES)}", name="ck_research_runs_mode"
        ),
        CheckConstraint(
            f"status IN {_in_clause(RUN_STATUSES)}", name="ck_research_runs_status"
        ),
        CheckConstraint("state_version >= 0", name="ck_research_runs_state_version"),
        CheckConstraint("last_event_seq >= 0", name="ck_research_runs_last_event_seq"),
        CheckConstraint(
            "current_report_revision IS NULL OR current_report_revision > 0",
            name="ck_research_runs_report_revision",
        ),
        CheckConstraint(
            " AND ".join(
                [
                    _hash_check("request_hash"),
                    f"(fingerprint IS NULL OR {_hash_check('fingerprint')})",
                    (
                        "(idempotency_key_hash IS NULL OR "
                        f"{_hash_check('idempotency_key_hash')})"
                    ),
                    f"(finalize_sha256 IS NULL OR {_hash_check('finalize_sha256')})",
                ]
            ),
            name="ck_research_runs_hash_format",
        ),
        CheckConstraint(
            f"(status IN {_in_clause(ACTIVE_RUN_STATUSES)}"
            " AND finished_at IS NULL"
            " AND error IS NULL"
            " AND finalize_token IS NULL"
            " AND finalize_sha256 IS NULL)"
            " OR "
            f"(status IN {_in_clause(TERMINAL_RUN_STATUSES)}"
            " AND finished_at IS NOT NULL"
            " AND finalize_token IS NOT NULL"
            " AND finalize_sha256 IS NOT NULL)",
            name="ck_research_runs_terminal_consistency",
        ),
        # 报告指针完整性：延迟到提交时校验，允许同一事务内先插报告再更新指针。
        # use_alter=True 让 create_all/autogenerate 以 ALTER TABLE 添加该约束，
        # 避免 runs ↔ reports 的循环依赖导致建表顺序无解。
        ForeignKeyConstraint(
            ["run_id", "current_report_revision"],
            ["research_reports.run_id", "research_reports.revision"],
            name="fk_research_runs_current_report",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index(
            "uq_research_runs_active_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text(
                "mode = 'live' AND status IN ('queued', 'running')"
                " AND fingerprint IS NOT NULL"
            ),
        ),
        Index(
            "uq_research_runs_idempotency_key",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("idempotency_key_hash IS NOT NULL"),
        ),
        Index("ix_research_runs_created_at", "created_at"),
    )


class ResearchReportORM(Base):
    """不可变报告版本；revision 由终态命令在事务外预分配。"""

    __tablename__ = "research_reports"

    run_id: Mapped[str] = mapped_column(
        CHAR(32),
        ForeignKey(
            "research_runs.run_id",
            ondelete="RESTRICT",
            name="fk_research_reports_run",
        ),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_token: Mapped[str] = mapped_column(CHAR(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_kind: Mapped[str] = mapped_column(Text, nullable=False)
    public_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    authority_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_reference: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "revision_token", name="uq_research_reports_token"),
        CheckConstraint("revision > 0", name="ck_research_reports_revision"),
        CheckConstraint(
            f"snapshot_kind IN {_in_clause(SNAPSHOT_KINDS)}",
            name="ck_research_reports_kind",
        ),
        CheckConstraint("schema_version >= 1", name="ck_research_reports_schema"),
        CheckConstraint(_hash_check("payload_sha256"), name="ck_research_reports_hash"),
    )


class ResearchEventORM(Base):
    """已脱敏的公开事件；seq 由 Manager 串行分配，数据库只做约束。"""

    __tablename__ = "research_events"

    run_id: Mapped[str] = mapped_column(
        CHAR(32),
        ForeignKey(
            "research_runs.run_id",
            ondelete="RESTRICT",
            name="fk_research_events_run",
        ),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_type: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[object | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    received_at: Mapped[object] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    __table_args__ = (
        CheckConstraint("seq > 0", name="ck_research_events_seq"),
        CheckConstraint(
            f"node_type IN {_in_clause(NODE_TYPES)}",
            name="ck_research_events_node_type",
        ),
        CheckConstraint(
            f"status IN {_in_clause(NODE_STATUSES)}",
            name="ck_research_events_status",
        ),
        CheckConstraint(
            f"event_type IN {_in_clause(EVENT_TYPES)}",
            name="ck_research_events_event_type",
        ),
        CheckConstraint(
            f"source IN {_in_clause(EVENT_SOURCES)}",
            name="ck_research_events_source",
        ),
        CheckConstraint("length(summary) > 0", name="ck_research_events_summary"),
        CheckConstraint(_hash_check("payload_sha256"), name="ck_research_events_hash"),
    )
