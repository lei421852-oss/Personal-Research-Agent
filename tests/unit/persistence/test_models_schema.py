"""离线核对 ORM 元数据：表、列、命名约束、部分唯一索引与延迟外键。

不连接数据库；只在 PostgreSQL 方言上编译 DDL 结构。
"""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateTable

from app.persistence import models

DIALECT = postgresql.dialect()


def ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=DIALECT))


def constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def index_by_name(table) -> dict:
    return {index.name: index for index in table.indexes}


def test_three_tables_only():
    assert set(models.Base.metadata.tables) == {
        "research_runs",
        "research_events",
        "research_reports",
    }


def test_run_columns_match_contract():
    table = models.ResearchRunORM.__table__
    assert set(table.columns.keys()) == {
        "run_id",
        "mode",
        "status",
        "request_payload",
        "request_hash",
        "idempotency_key_hash",
        "fingerprint",
        "effective_paper_ids",
        "owner_instance_id",
        "state_version",
        "last_event_seq",
        "current_report_revision",
        "public_state",
        "error",
        "metrics",
        "cancel_requested",
        "event_limit_reached",
        "finalize_token",
        "finalize_sha256",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    }
    assert table.primary_key.name == "pk_research_runs"


def test_event_and_report_columns_match_contract():
    events = models.ResearchEventORM.__table__
    assert set(events.columns.keys()) == {
        "run_id",
        "seq",
        "node_type",
        "node_id",
        "status",
        "event_type",
        "source",
        "summary",
        "details",
        "occurred_at",
        "received_at",
        "payload_sha256",
    }
    assert events.primary_key.name == "pk_research_events"

    reports = models.ResearchReportORM.__table__
    assert set(reports.columns.keys()) == {
        "run_id",
        "revision",
        "revision_token",
        "schema_version",
        "snapshot_kind",
        "public_payload",
        "payload_sha256",
        "authority_payload",
        "source_reference",
        "created_at",
    }
    assert reports.primary_key.name == "pk_research_reports"


def test_run_constraint_names():
    names = constraint_names(models.ResearchRunORM.__table__)
    assert {
        "ck_research_runs_mode",
        "ck_research_runs_status",
        "ck_research_runs_state_version",
        "ck_research_runs_last_event_seq",
        "ck_research_runs_report_revision",
        "ck_research_runs_hash_format",
        "ck_research_runs_terminal_consistency",
        "fk_research_runs_current_report",
    } <= names


def test_event_constraint_names():
    names = constraint_names(models.ResearchEventORM.__table__)
    assert {
        "ck_research_events_seq",
        "ck_research_events_node_type",
        "ck_research_events_status",
        "ck_research_events_event_type",
        "ck_research_events_source",
        "ck_research_events_summary",
        "ck_research_events_hash",
        "fk_research_events_run",
    } <= names


def test_report_constraint_names():
    names = constraint_names(models.ResearchReportORM.__table__)
    assert {
        "uq_research_reports_token",
        "ck_research_reports_revision",
        "ck_research_reports_kind",
        "ck_research_reports_schema",
        "ck_research_reports_hash",
        "fk_research_reports_run",
    } <= names


def test_terminal_consistency_requires_error_null_when_active():
    table = models.ResearchRunORM.__table__
    check = next(
        c
        for c in table.constraints
        if c.name == "ck_research_runs_terminal_consistency"
    )
    sql = str(check.sqltext)
    active_branch, terminal_branch = sql.split(" OR ")
    assert "error IS NULL" in active_branch
    assert "finished_at IS NULL" in active_branch
    assert "finalize_token IS NULL" in active_branch
    assert "finalize_sha256 IS NULL" in active_branch
    assert "finished_at IS NOT NULL" in terminal_branch
    assert "finalize_token IS NOT NULL" in terminal_branch
    assert "finalize_sha256 IS NOT NULL" in terminal_branch
    # 终态分支不约束 error：completed 可以合法携带 report_language_timeout。
    assert "error IS NOT NULL" not in terminal_branch


def test_active_fingerprint_index_predicate():
    index = index_by_name(models.ResearchRunORM.__table__)[
        "uq_research_runs_active_fingerprint"
    ]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["fingerprint"]
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "mode = 'live'" in predicate
    assert "status IN ('queued', 'running')" in predicate
    assert "fingerprint IS NOT NULL" in predicate


def test_idempotency_index_predicate():
    index = index_by_name(models.ResearchRunORM.__table__)[
        "uq_research_runs_idempotency_key"
    ]
    assert index.unique is True
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert predicate.strip() == "idempotency_key_hash IS NOT NULL"


def test_pointer_foreign_key_is_deferred_and_alter_based():
    table = models.ResearchRunORM.__table__
    fk = next(
        c for c in table.constraints if c.name == "fk_research_runs_current_report"
    )
    assert fk.deferrable is True
    assert fk.initially == "DEFERRED"
    assert fk.use_alter is True
    assert [column.name for column in fk.columns] == [
        "run_id",
        "current_report_revision",
    ]
    assert [element.column.table.name for element in fk.elements] == [
        "research_reports",
        "research_reports",
    ]
    alter = str(AddConstraint(fk).compile(dialect=DIALECT))
    assert "DEFERRABLE INITIALLY DEFERRED" in alter


def test_run_ddl_excludes_cyclic_constraint_but_keeps_checks():
    sql = ddl(models.ResearchRunORM.__table__)
    assert "JSONB" in sql
    assert "CHECK (mode IN ('live', 'replay'))" in sql
    # 循环外键以 ALTER TABLE 形式单独发出，不进入 CREATE TABLE。
    assert "fk_research_runs_current_report" not in sql


def test_event_ddl_enumerations():
    sql = ddl(models.ResearchEventORM.__table__)
    assert "'MainAgent'" in sql
    assert "'QueryLanguage'" in sql
    assert "seq > 0" in sql
    assert "length(summary) > 0" in sql


def test_report_ddl_unique_token_and_kind():
    sql = ddl(models.ResearchReportORM.__table__)
    assert "uq_research_reports_token" in sql
    assert "snapshot_kind IN ('answer_first', 'execution_report')" in sql


def test_schema_identifiers():
    assert models.SCHEMA_VERSION == 1
    assert models.EXPECTED_ALEMBIC_REVISION == "h2_0001"
