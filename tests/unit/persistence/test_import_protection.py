"""默认禁用时不得把 SQLAlchemy 或业务 ORM 带进运行时。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

PROBE = (
    "import sys\n"
    "import app.workbench\n"
    "print(int('sqlalchemy' in sys.modules))\n"
    "print(int('asyncpg' in sys.modules))\n"
    "print(int('app.persistence.models' in sys.modules))\n"
)


def test_workbench_import_does_not_load_persistence_stack():
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    loaded = result.stdout.split()
    assert loaded == ["0", "0", "0"], result.stdout + result.stderr
