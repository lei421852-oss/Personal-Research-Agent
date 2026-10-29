"""只读核验新增ORM与历史迁移的兼容性，不连接数据库。"""

import importlib.util
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.persistence.memory_models import MEMORY_REVISION, AgentMemoryORM, MemoryBase
from app.persistence.models import Base


def test_memory_metadata_does_not_pollute_original_three_tables():
    assert set(Base.metadata.tables) == {
        "research_runs",
        "research_events",
        "research_reports",
    }
    assert set(MemoryBase.metadata.tables) == {"agent_memories"}


def test_memory_ddl_contains_scope_versions_source_and_soft_delete_constraints():
    sql = str(
        CreateTable(AgentMemoryORM.__table__).compile(dialect=postgresql.dialect())
    )
    for value in [
        "uq_agent_memory_version",
        "fk_agent_memory_previous",
        "ck_agent_memory_source",
        "ck_agent_memory_deleted",
        "TIMESTAMP WITH TIME ZONE",
        "JSONB",
    ]:
        assert value in sql
    indexes = {
        index.name: str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for index in AgentMemoryORM.__table__.indexes
    }
    assert "WHERE status = 'active'" in indexes["uq_agent_memory_active"]


def test_new_migration_is_additive_and_old_snapshot_unchanged():
    path = Path("migrations/versions/p2_0001_agent_memory.py")
    spec = importlib.util.spec_from_file_location("p2_memory_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == MEMORY_REVISION
    assert module.down_revision == "h2_0001"
    source = path.read_text()
    assert "from app." not in source
    assert 'op.drop_table("agent_memories")' in source
    assert 'drop_table("research_' not in source
