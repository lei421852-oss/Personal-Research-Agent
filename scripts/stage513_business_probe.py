import asyncio
import json
import os
import re
import subprocess
from urllib.parse import urlsplit

from pydantic import SecretStr
from sqlalchemy import text

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import PersistenceLatch
from app.persistence.database import DatabaseResource
from app.persistence.writer import RunWriter, WriterFailed
from tests.unit.persistence.test_writer import append

if not __debug__:
    raise RuntimeError("Optimized Python is not permitted for this safety probe")
if os.environ.get("STAGE513_ISOLATED_TEST_AUTHORIZED") != "yes":
    raise RuntimeError("Explicit isolated test authorization required")

url = os.environ["H2_TEST_PERSISTENCE_DATABASE_URL"]
name = os.environ["PERSISTENCE_TEST_CONTAINER"]
u = urlsplit(url)
if (u.hostname, u.port, u.path, u.username, u.query) != (
    "127.0.0.1",
    55438,
    "/latch_test",
    "h2_test",
    "ssl=disable",
):
    raise RuntimeError("Unexpected isolated database target")
if not re.fullmatch(r"codex-stage513-business-[0-9a-f]{12}", name):
    raise RuntimeError("Unexpected isolated container name")
inspection = subprocess.run(
    ["docker", "inspect", name], capture_output=True, text=True, check=True
)
container_info = json.loads(inspection.stdout)[0]
ports = container_info["NetworkSettings"]["Ports"]["5432/tcp"]
if (
    container_info["Config"]["Labels"].get("codex.purpose") != "stage513-business"
    or len(ports) != 1
    or ports[0].get("HostIp") != "127.0.0.1"
    or ports[0].get("HostPort") != "55438"
):
    raise RuntimeError("Isolated container identity or port mismatch")
seen = {"commit_sent": False, "commit_complete_blocked": False, "attempts": 0}


async def proxy_client(reader, writer):
    upstream_r, upstream_w = await asyncio.open_connection("127.0.0.1", 55438)
    try:
        prefix = await reader.readexactly(4)
        length = int.from_bytes(prefix, "big")
        upstream_w.write(prefix + await reader.readexactly(length - 4))
        await upstream_w.drain()

        async def forward(source, target, client):
            try:
                while True:
                    kind = await source.readexactly(1)
                    size = await source.readexactly(4)
                    data = await source.readexactly(int.from_bytes(size, "big") - 4)
                    if client and kind in (b"Q", b"P") and b"COMMIT" in data.upper():
                        seen["commit_sent"] = True
                    if (
                        not client
                        and seen["commit_sent"]
                        and kind == b"C"
                        and data.startswith(b"COMMIT")
                    ):
                        seen["commit_complete_blocked"] = True
                        writer.transport.abort()
                        upstream_w.transport.abort()
                        return
                    target.write(kind + size + data)
                    await target.drain()
            except (
                asyncio.IncompleteReadError,
                ConnectionError,
                asyncio.CancelledError,
            ):
                pass

        tasks = [
            asyncio.create_task(forward(reader, upstream_w, True)),
            asyncio.create_task(forward(upstream_r, writer, False)),
        ]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        upstream_w.close()
        writer.close()


from datetime import UTC, datetime

from app.persistence.coordinator import TerminalCoordinator
from app.persistence.repository import (
    FinalizeOutcome,
    ReconcileOutcome,
    ResearchRepository,
)
from app.persistence.serialization import (
    build_finalize_command,
    build_report_command,
    sha256_hex,
)


async def main():
    direct = DatabaseResource(
        DatabaseSettings(enabled=True, url=SecretStr(url), pool_size=2, max_overflow=0)
    )
    await direct.start()
    direct_repo = ResearchRepository(direct)
    payload = {"question": "synthetic"}
    for rid in ("a" * 32, "b" * 32):
        await direct_repo.create_run(
            run_id=rid,
            mode="live",
            request_payload=payload,
            request_hash=sha256_hex(payload),
            owner_instance_id="c" * 32,
        )
    server = await asyncio.start_server(proxy_client, "127.0.0.1", 55439)
    proxy_url = url.replace(":55438/", ":55439/")
    resource = DatabaseResource(
        DatabaseSettings(
            enabled=True,
            url=SecretStr(proxy_url),
            pool_size=1,
            max_overflow=0,
            connect_timeout=3,
        )
    )
    await resource.start()
    repo = ResearchRepository(resource)
    latch = PersistenceLatch()
    writer = RunWriter(repo, latch=latch, write_timeout=8, max_attempts=2)
    writer.start()
    try:
        writer.submit(append(1, 0))
        try:
            await writer.flush(timeout=15)
            raise AssertionError("commit acknowledgment loss reported successful")
        except WriterFailed:
            pass
        events = await direct_repo.get_events("a" * 32)
        run = await direct_repo.get_run("a" * 32)
        assert (
            seen["commit_complete_blocked"]
            and len(events) == 1
            and run.last_event_seq == 1
        )
        assert writer.stats.retried == 0 and latch.tripped
        print(
            json.dumps(
                {
                    "phase": "event_commit_ack_lost",
                    "event_count": len(events),
                    "last_event_seq": run.last_event_seq,
                    "writer_retries": writer.stats.retried,
                    "latched": latch.tripped,
                    "confirmation_dropped": seen["commit_complete_blocked"],
                }
            ),
            flush=True,
        )
    finally:
        await writer.close()
        await resource.close()
    seen["commit_sent"] = False
    seen["commit_complete_blocked"] = False
    resource = DatabaseResource(
        DatabaseSettings(
            enabled=True,
            url=SecretStr(proxy_url),
            pool_size=1,
            max_overflow=0,
            connect_timeout=3,
        )
    )
    await resource.start()
    repo = ResearchRepository(resource)
    coordinator = TerminalCoordinator(repo)
    report = build_report_command(
        expected_current_revision=None,
        revision_token="d" * 32,
        schema_version=1,
        snapshot_kind="answer_first",
        public_payload={"answer": "synthetic"},
    )
    cmd = build_finalize_command(
        run_id="b" * 32,
        finalize_token="e" * 32,
        expected_state_version=0,
        status="completed",
        finished_at=datetime.now(UTC),
        error=None,
        metrics={},
        expected_last_event_seq=0,
        final_last_event_seq=0,
        expected_current_revision=None,
        report=report,
    )
    try:
        outcome = await coordinator.finalize(cmd)
        assert (
            outcome is FinalizeOutcome.UNKNOWN
            and coordinator.latch.tripped
            and seen["commit_complete_blocked"]
        )
        row = await direct_repo.get_run("b" * 32)
        saved = await direct_repo.get_current_report("b" * 32)
        assert (
            row.status == "completed"
            and row.finalize_token == cmd.finalize_token
            and saved.revision_token == report.revision_token
        )
        coordinator.repository = direct_repo
        reconciled = await coordinator.reconcile(
            "b" * 32, token=cmd.finalize_token, sha256=cmd.finalize_sha256
        )
        assert (
            reconciled is ReconcileOutcome.CONFIRMED and not coordinator.latch.tripped
        )
        async with direct.session() as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM research_reports WHERE run_id='"
                        + "b" * 32
                        + "'"
                    )
                )
            ).scalar_one()
        assert count == 1
        print(
            json.dumps(
                {
                    "phase": "finalize_commit_ack_lost",
                    "outcome": outcome.value,
                    "database_status": row.status,
                    "report_count": count,
                    "reconcile": reconciled.value,
                    "latch_after_reconcile": coordinator.latch.tripped,
                    "confirmation_dropped": seen["commit_complete_blocked"],
                }
            ),
            flush=True,
        )
    finally:
        await coordinator.shutdown()
        await resource.close()
        server.close()
        await server.wait_closed()
        await direct.close()


asyncio.run(main())
