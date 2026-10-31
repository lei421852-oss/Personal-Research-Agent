"""离线验证故障注入脚本的授权与目标门控。"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "scripts" / "stage513_business_probe.py"
LAUNCHER = ROOT / "scripts" / "verify_stage513_postgresql.py"


def run_script(path, *flags, **extra):
    env = os.environ.copy()
    env.pop("STAGE513_ISOLATED_TEST_AUTHORIZED", None)
    env.pop("H2_TEST_PERSISTENCE_DATABASE_URL", None)
    env.pop("PERSISTENCE_TEST_CONTAINER", None)
    env.pop("PYTHONOPTIMIZE", None)
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return subprocess.run(
        [sys.executable, "-B", *flags, str(path)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_launcher_requires_explicit_authorization():
    result = run_script(LAUNCHER)
    assert result.returncode != 0
    assert "authorization required" in result.stderr.lower()


def test_probe_requires_explicit_authorization():
    result = run_script(PROBE)
    assert result.returncode != 0
    assert "authorization required" in result.stderr.lower()


def test_probe_rejects_optimized_python():
    result = run_script(PROBE, "-O", STAGE513_ISOLATED_TEST_AUTHORIZED="yes")
    assert result.returncode != 0
    assert "optimized python" in result.stderr.lower()


def test_probe_rejects_unexpected_database_before_docker():
    result = run_script(
        PROBE,
        STAGE513_ISOLATED_TEST_AUTHORIZED="yes",
        H2_TEST_PERSISTENCE_DATABASE_URL="postgresql+asyncpg://h2_test:x@127.0.0.1:55438/agent_learning?ssl=disable",
        PERSISTENCE_TEST_CONTAINER="codex-stage513-business-0123456789ab",
    )
    assert result.returncode != 0
    assert "unexpected isolated database target" in result.stderr.lower()
