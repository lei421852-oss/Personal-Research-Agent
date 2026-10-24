"""离线核对迁移骨架：revision 身份、降级顺序、独立连接串与无秘密的 ini。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.persistence import models
from app.persistence.migrations_url import ENV_VAR, migration_url

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "versions" / "h2_0001_research_persistence.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_file_exists_and_matches_expected_revision():
    assert MIGRATION.is_file()
    module = load(MIGRATION, "h2_0001")
    assert module.revision == models.EXPECTED_ALEMBIC_REVISION
    assert module.down_revision is None
    assert module.branch_labels is None
    assert module.depends_on is None


def test_migration_has_both_directions():
    module = load(MIGRATION, "h2_0001_callables")
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_migration_does_not_import_app_models():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from app.persistence.models" not in source
    assert "import app.persistence.models" not in source


def test_alembic_ini_has_no_url_and_points_to_migrations():
    ini = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert "script_location = migrations" in ini
    url_lines = [
        line.strip()
        for line in ini.splitlines()
        if line.strip().startswith("sqlalchemy.url")
    ]
    assert url_lines == ["sqlalchemy.url ="]
    assert "postgresql" not in ini


def test_env_py_uses_dedicated_variable_only():
    source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "migration_url" in source
    assert "H2_DATABASE_URL" not in source
    assert "create_all" not in source
    assert "stamp" not in source


def test_migration_url_requires_the_variable():
    with pytest.raises(RuntimeError):
        migration_url({})
    with pytest.raises(RuntimeError):
        migration_url({ENV_VAR: "   "})


def test_migration_url_rejects_other_drivers():
    with pytest.raises(RuntimeError):
        migration_url({ENV_VAR: "postgresql://user:pw@localhost/db"})
    with pytest.raises(RuntimeError):
        migration_url({ENV_VAR: "sqlite+aiosqlite:///tmp/x.db"})


def test_migration_url_accepts_asyncpg():
    url = "postgresql+asyncpg://user:pw@localhost:5432/personal_agent"
    assert migration_url({ENV_VAR: url}) == url


def test_downgrade_drops_constraint_before_report_table():
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    order = [
        downgrade.index('drop_table("research_events")'),
        downgrade.index('"fk_research_runs_current_report"'),
        downgrade.index('drop_table("research_reports")'),
        downgrade.index('drop_table("research_runs")'),
    ]
    assert order == sorted(order)
