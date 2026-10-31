import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
if os.environ.get("STAGE517_ISOLATED_TEST_AUTHORIZED") != "yes":
    raise SystemExit("Explicit isolated test authorization required")
out = root / "artifacts" / ("stage517-ownership-" + time.strftime("%Y%m%d-%H%M%S"))
out.mkdir(parents=True, exist_ok=False)
name = "codex-stage517-ownership-" + uuid.uuid4().hex[:12]
password = secrets.token_hex(24)
env = os.environ.copy()
env.pop("PYTHONOPTIMIZE", None)
env["POSTGRES_PASSWORD"] = password
env.pop("H2_TEST_PERSISTENCE_DATABASE_URL", None)
summary = {
    "test_target": "isolated Docker PostgreSQL 16, loopback 55438",
    "actual_live_model_calls": 0,
    "actual_arxiv_calls": 0,
    "phases": [],
    "cleanup": "pending",
}


def safe(value):
    return value.replace(password, "<redacted>").replace(
        "postgresql+asyncpg://h2_test:<redacted>@127.0.0.1:55438/personal_agent_test_stage517?ssl=disable",
        "<redacted-test-url>",
    )


def record(label, proc, start):
    text = safe(proc.stdout or "")
    (out / (label + ".log")).write_text(text, encoding="utf-8")
    summary["phases"].append(
        {
            "name": label,
            "exit_code": proc.returncode,
            "elapsed_seconds": round(time.monotonic() - start, 2),
        }
    )
    print(label, "exit", proc.returncode, flush=True)
    if proc.returncode:
        print("last_lines", *text.splitlines()[-8:], sep="\n", flush=True)
    return proc.returncode == 0


def run(label, args, process_env, timeout=150):
    print("START", label, flush=True)
    started = time.monotonic()
    proc = subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
        args,
        cwd=root,
        env=process_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return record(label, proc, started)


try:
    started = time.monotonic()
    proc = subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
        [
            "docker",
            "run",
            "-d",
            "--label",
            "codex.purpose=stage517-ownership",
            "--name",
            name,
            "-p",
            "127.0.0.1:55438:5432",
            "-e",
            "POSTGRES_USER=h2_test",
            "-e",
            "POSTGRES_DB=personal_agent_test_stage517",
            "-e",
            "POSTGRES_PASSWORD",
            "-e",
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
            "postgres:16-alpine",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )
    if not record("create_container", proc, started):
        raise RuntimeError("container_create_failed")
    inspect = subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "codex.purpose"}}|{{range .Mounts}}{{.Type}}:{{.Destination}};{{end}}|{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostIp}}:{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
            name,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=15,
    )
    value = inspect.stdout.strip()
    if inspect.returncode or not value.startswith(
        "stage517-ownership|volume:/var/lib/postgresql/data;|127.0.0.1:55438"
    ):
        raise RuntimeError("container_target_verification_failed")
    print("target_verified", flush=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        ready = subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
            [
                "docker",
                "exec",
                name,
                "pg_isready",
                "-U",
                "h2_test",
                "-d",
                "personal_agent_test_stage517",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("postgres_not_ready")
    url = (
        "postgresql+asyncpg://h2_test:"
        + password
        + "@127.0.0.1:55438/personal_agent_test_stage517?ssl=disable"
    )
    assert (urlsplit(url).hostname, urlsplit(url).port, urlsplit(url).path) == (
        "127.0.0.1",
        55438,
        "/personal_agent_test_stage517",
    )
    env["H2_MIGRATION_DATABASE_URL"] = url
    if not run(
        "isolated_h2_migration",
        [".venv/bin/alembic", "upgrade", "h2_0001"],
        env,
        timeout=80,
    ):
        raise RuntimeError("isolated_migration_failed")
    env["H2_TEST_PERSISTENCE_DATABASE_URL"] = url
    env["H2_TEST_PERSISTENCE_AUTHORIZED"] = "yes"
    env["PERSISTENCE_TEST_CONTAINER"] = name
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(root)
    for key in (
        "B1_TEST_ARXIV_AUTHORIZED",
        "B2_TEST_ARXIV_AUTHORIZED",
        "P2_TEST_MEMORY_AUTHORIZED",
        "LLM_API_KEY",
        "VLM_API_KEY",
    ):
        env.pop(key, None)
    # 旧 Repository 测试明确校验 h2_0001，先在干净基线上运行。
    if not run(
        "existing_postgresql_regression",
        [
            ".venv/bin/python",
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--tb=short",
            "tests/integration/test_repository_postgresql.py",
            "tests/integration/test_writer_postgresql.py",
        ],
        env,
        timeout=150,
    ):
        raise RuntimeError("postgresql_regression_failed")
    if not run(
        "isolated_head_migration",
        [".venv/bin/alembic", "upgrade", "head"],
        env,
        timeout=80,
    ):
        raise RuntimeError("isolated_head_migration_failed")
    env.pop("H2_MIGRATION_DATABASE_URL", None)
    env.pop("H2_TEST_PERSISTENCE_AUTHORIZED", None)
    if not run(
        "commit_probe",
        [".venv/bin/python", "-B", "scripts/stage517_owner_probe.py"],
        env,
        timeout=80,
    ):
        raise RuntimeError("commit_probe_failed")
    summary["result"] = "stage517_probe_pass"
except Exception as exc:  # noqa: BLE001 - 确保记录失败并清理独立容器
    summary["result"] = "error"
    summary["error_type"] = type(exc).__name__
    summary["error_code"] = (
        str(exc) if isinstance(exc, RuntimeError) else "execution_failed"
    )
    print("STOP", summary["error_code"], flush=True)
finally:
    try:
        check = subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "codex.purpose"}}',
                name,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        if check.returncode == 0 and check.stdout.strip() == "stage517-ownership":
            subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
                ["docker", "unpause", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            rm = subprocess.run(  # noqa: PLW1510 - 按退出码记录并继续清理
                ["docker", "rm", "-f", "-v", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
            summary["cleanup"] = (
                "container_and_anonymous_volume_removed"
                if rm.returncode == 0
                else "cleanup_failed"
            )
        elif check.returncode != 0:
            summary["cleanup"] = "container_absent_or_unverifiable"
        else:
            summary["cleanup"] = "identity_mismatch_not_removed"
    except Exception:  # noqa: BLE001 - 清理失败必须明确记录
        summary["cleanup"] = "cleanup_failed"
    (out / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "RESULT",
        json.dumps(summary, ensure_ascii=False),
        "evidence_dir",
        out,
        flush=True,
    )

if (
    summary.get("result") != "stage517_probe_pass"
    or summary["cleanup"] != "container_and_anonymous_volume_removed"
):
    raise SystemExit(1)
