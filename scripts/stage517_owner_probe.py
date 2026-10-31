"""仅隔离 PostgreSQL：验证 owner 和运行资格，不调用真实模型。"""

import asyncio
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pydantic import SecretStr
from sqlalchemy import text

from app.persistence.config import DatabaseSettings
from app.persistence.coordinator import TerminalCoordinator
from app.persistence.database import DatabaseResource
from app.persistence.lifecycle import RunPersistence
from app.persistence.repository import (
    ConflictError,
    FinalizeOutcome,
    ResearchRepository,
    TransitionOutcome,
)
from app.persistence.serialization import (
    build_finalize_command,
    build_report_command,
    sha256_hex,
)
from app.research.run_manager import RunManagerError, _Run
from tests.unit.persistence.test_writer import event
from tests.unit.research.test_lifecycle import HappyRunner, manager, request, settle

if not __debug__ or os.environ.get("STAGE517_ISOLATED_TEST_AUTHORIZED") != "yes":
    raise RuntimeError("Explicit isolated test authorization and assertions required")
url = os.environ["H2_TEST_PERSISTENCE_DATABASE_URL"]
name = os.environ["PERSISTENCE_TEST_CONTAINER"]
u = urlsplit(url)
if (u.hostname, u.port, u.path, u.username, u.query) != (
    "127.0.0.1",
    55438,
    "/personal_agent_test_stage517",
    "h2_test",
    "ssl=disable",
):
    raise RuntimeError("Unexpected isolated database target")
if not re.fullmatch(r"codex-stage517-ownership-[0-9a-f]{12}", name):
    raise RuntimeError("Unexpected isolated container name")
info = json.loads(
    subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True, check=True
    ).stdout
)[0]
ports = info["NetworkSettings"]["Ports"]["5432/tcp"]
if (
    info["Config"]["Labels"].get("codex.purpose") != "stage517-ownership"
    or len(ports) != 1
    or ports[0].get("HostIp") != "127.0.0.1"
    or ports[0].get("HostPort") != "55438"
):
    raise RuntimeError("Container identity or port mismatch")

OWNER = "a" * 32
OTHER = "b" * 32
RUN = "c" * 32
RACE = "d" * 32


def command(run_id):
    return build_finalize_command(
        run_id=run_id,
        finalize_token="e" * 32,
        expected_state_version=0,
        status="failed",
        finished_at=datetime.now(UTC),
        error={"code": "fixture"},
        metrics={},
        expected_last_event_seq=0,
        final_last_event_seq=0,
        expected_current_revision=None,
    )


async def seed(repo, run_id):
    payload = {"question": "synthetic"}
    await repo.create_run(
        run_id=run_id,
        mode="live",
        request_payload=payload,
        request_hash=sha256_hex(payload),
        owner_instance_id=OWNER,
    )


async def main():
    resource = DatabaseResource(
        DatabaseSettings(enabled=True, url=SecretStr(url), pool_size=2, max_overflow=0)
    )
    await resource.start()
    repo = ResearchRepository(resource)
    try:
        await seed(repo, RUN)
        wrong = await repo.transition_run(
            RUN,
            expected_version=0,
            expected_owner_id=OTHER,
            from_statuses=("queued",),
            to_status="running",
        )
        assert wrong.outcome is TransitionOutcome.OWNER_CONFLICT
        try:
            await repo.append_events(
                RUN, [event(1)], expected_last_seq=0, expected_owner_id=OTHER
            )
            raise AssertionError("wrong owner appended event")
        except ConflictError as exc:
            assert exc.code == "owner_conflict"
        report = build_report_command(
            expected_current_revision=None,
            revision_token="9" * 32,
            schema_version=1,
            snapshot_kind="answer_first",
            public_payload={"status": "completed"},
        )
        try:
            await repo.save_report_revision(RUN, report, expected_owner_id=OTHER)
            raise AssertionError("wrong owner saved report")
        except ConflictError as exc:
            assert exc.code == "owner_conflict"
        assert await repo.get_current_report(RUN) is None
        bad_finish = await repo.finalize_run(command(RUN), expected_owner_id=OTHER)
        assert bad_finish.outcome is FinalizeOutcome.OWNER_CONFLICT
        row = await repo.get_run(RUN)
        assert (
            row.status == "queued"
            and row.state_version == 0
            and row.owner_instance_id == OWNER
        )
        assert await repo.get_events(RUN) == []
        print(
            json.dumps(
                {
                    "phase": "wrong_owner",
                    "transition": wrong.outcome.value,
                    "finalize": bad_finish.outcome.value,
                    "event_count": 0,
                    "report_count": 0,
                    "state_unchanged": True,
                }
            ),
            flush=True,
        )

        await seed(repo, RACE)
        first_runner, second_runner = HappyRunner(), HappyRunner()
        first_lifecycle = RunPersistence(repo, owner_instance_id=OWNER)
        second_lifecycle = RunPersistence(repo, owner_instance_id=OWNER)
        first, second = (
            manager(first_runner, first_lifecycle),
            manager(second_runner, second_lifecycle),
        )
        run1 = _Run(RACE, "live", "synthetic", [], datetime.now(UTC).isoformat())
        run2 = _Run(RACE, "live", "synthetic", [], datetime.now(UTC).isoformat())
        await asyncio.gather(
            first._execute(run1, request()), second._execute(run2, request())
        )
        assert first_runner.calls + second_runner.calls == 1
        assert sum(int(x.execution_gate_failed) for x in (run1, run2)) == 1
        print(
            json.dumps(
                {
                    "phase": "concurrent_claim",
                    "runner_calls": first_runner.calls + second_runner.calls,
                    "rejected_executors": sum(
                        int(x.execution_gate_failed) for x in (run1, run2)
                    ),
                }
            ),
            flush=True,
        )
        await first.close()
        await second.close()

        # 同一 owner 的另一个 Task 在首次运行前取消，不得收尾已运行任务。
        cancel_id = "f" * 32
        await seed(repo, cancel_id)
        active = RunPersistence(repo, owner_instance_id=OWNER)
        assert await active.mark_running(cancel_id) is TransitionOutcome.UPDATED
        losing = RunPersistence(repo, owner_instance_id=OWNER)
        loser_runner = HappyRunner()
        loser = manager(loser_runner, losing)
        run = _Run(cancel_id, "live", "synthetic", [], datetime.now(UTC).isoformat())
        loser._runs[cancel_id] = run
        task = loser._schedule(run, request())
        run.task = task
        task.add_done_callback(lambda done: loser._finished(run, done))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        if run.finalize_task:
            await run.finalize_task
        state = await repo.get_run(cancel_id)
        assert state.status == "running" and loser_runner.calls == 0
        assert run.persisted is False
        assert run.persistence_error == "unclaimed_run_not_queued"
        print(
            json.dumps(
                {
                    "phase": "unstarted_cancel",
                    "status": state.status,
                    "loser_runner_calls": loser_runner.calls,
                    "loser_persisted": run.persisted,
                }
            ),
            flush=True,
        )

        # 新实例只读识别非终态历史，不自动安排执行。
        restarted_runner = HappyRunner()
        restarted = manager(
            restarted_runner, RunPersistence(repo, owner_instance_id=OTHER)
        )
        try:
            await restarted._settle_replay(state, "unused")
            raise AssertionError("foreign active run attached")
        except RunManagerError as exc:
            assert exc.code == "run_not_attached"
        assert restarted_runner.calls == 0
        assert (await repo.get_run(cancel_id)).status == "running"
        print(
            json.dumps(
                {
                    "phase": "restart",
                    "result": "run_not_attached",
                    "runner_calls": 0,
                    "status": "running",
                }
            ),
            flush=True,
        )
        await loser.close()
        await restarted.close()
        await active.close()

        runner = HappyRunner()
        lifecycle = RunPersistence(
            repo, owner_instance_id=OWNER, coordinator=TerminalCoordinator(repo)
        )
        mgr = manager(runner, lifecycle)
        try:
            result = await mgr.create(request("synthetic complete"))
            await settle(mgr, result["run_id"])
            state = await repo.get_run(result["run_id"])
            events = await repo.get_events(result["run_id"])
            assert runner.calls == 1 and state.status == "completed" and len(events) > 0
            assert mgr._runs[result["run_id"]].persisted
            print(
                json.dumps(
                    {
                        "phase": "full_lifecycle",
                        "runner_calls": runner.calls,
                        "status": state.status,
                        "event_count": len(events),
                        "persisted": True,
                    }
                ),
                flush=True,
            )
        finally:
            await mgr.close()
        async with resource.session() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM research_runs WHERE run_id=:rid"),
                    {"rid": RUN},
                )
            ).scalar_one()
        assert count == 1
    finally:
        await resource.close()


asyncio.run(main())
