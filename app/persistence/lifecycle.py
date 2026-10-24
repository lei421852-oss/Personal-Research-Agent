"""Manager 与持久化组件的适配层：创建、running、事件与终态命令。

本模块只做「快照 → 不可变命令」的映射，不改变研究执行顺序或对外 DTO。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.persistence.repository import (
    EventCommand,
    ResearchRepository,
    RunRecord,
)
from app.persistence.serialization import (
    build_finalize_command,
    build_report_command,
    sha256_hex,
)
from app.persistence.writer import AppendEvents, RunWriter


class RunPersistence:
    """实现 Manager 期望的 lifecycle 协议（start / mark_running / on_event / finish）。"""

    def __init__(
        self,
        repository: ResearchRepository,
        *,
        owner_instance_id: str,
        coordinator=None,
        latch=None,
        writer_factory=None,
    ) -> None:
        self.repository = repository
        self.owner_instance_id = owner_instance_id
        self.coordinator = coordinator
        self.latch = latch
        self._writer_factory = writer_factory
        self._writers: dict[str, RunWriter] = {}
        self._closed = False

    # ---- 生命周期 -------------------------------------------------------

    async def start(
        self,
        *,
        run_id: str,
        payload: dict,
        fingerprint: str | None,
        created_at: datetime | None = None,
    ) -> RunRecord:
        """创建 queued 记录并启动该 run 的 Writer；失败即拒绝调度。"""

        result = await self.repository.create_run(
            run_id=run_id,
            mode="live",
            request_payload=payload,
            request_hash=sha256_hex(payload),
            owner_instance_id=self.owner_instance_id,
            fingerprint=fingerprint,
            created_at=created_at,
        )
        writer = (
            self._writer_factory(run_id)
            if self._writer_factory is not None
            else RunWriter(self.repository, latch=self.latch)
        )
        writer.start()
        self._writers[run_id] = writer
        return result.run

    async def mark_running(self, run_id: str) -> None:
        record = await self.repository.get_run(run_id)
        if record is None or record.status != "queued":
            return
        await self.repository.transition_run(
            run_id,
            expected_version=record.state_version,
            from_statuses=("queued",),
            to_status="running",
        )

    def on_event(self, run_id: str, event) -> None:
        """把已分配序号、已脱敏的公开事件交给该 run 的 Writer。"""

        writer = self._writers.get(run_id)
        if writer is None:
            return
        occurred_at = None
        if event.timestamp:
            try:
                occurred_at = datetime.fromisoformat(event.timestamp)
            except ValueError:
                occurred_at = None
        writer.submit(
            AppendEvents(
                run_id=run_id,
                events=(
                    EventCommand.build(
                        seq=event.event_id,
                        node_type=event.node_type,
                        node_id=event.node_id,
                        status=event.status,
                        event_type=event.event_type,
                        source=event.source,
                        summary=event.summary,
                        details=event.details,
                        occurred_at=occurred_at,
                    ),
                ),
                expected_last_seq=event.event_id - 1,
            )
        )

    async def finish(self, run_id: str, snapshot: dict) -> None:
        """等待屏障后用协调器提交终态；未知结果只做只读核对。"""

        writer = self._writers.get(run_id)
        if writer is not None:
            await writer.flush()
        record = await self.repository.get_run(run_id)
        if record is None:
            raise RuntimeError("run_not_found")

        report = None
        public_report = snapshot.get("report")
        if isinstance(public_report, dict) and public_report:
            report = build_report_command(
                expected_current_revision=record.current_report_revision,
                revision_token=uuid4().hex,
                schema_version=1,
                snapshot_kind="answer_first",
                public_payload=public_report,
            )

        command = build_finalize_command(
            run_id=run_id,
            finalize_token=uuid4().hex,
            expected_state_version=record.state_version,
            status=_terminal_status(snapshot.get("status", "failed")),
            finished_at=snapshot.get("finished_at") or datetime.now(UTC),
            error=snapshot.get("error"),
            metrics=(public_report or {}).get("metrics", {}) if public_report else {},
            expected_last_event_seq=record.last_event_seq,
            final_last_event_seq=record.last_event_seq,
            expected_current_revision=record.current_report_revision,
            report=report,
        )

        if self.coordinator is None:
            await self.repository.finalize_run(command)
            return

        outcome = await self.coordinator.finalize(command)
        if outcome.value == "unknown":
            await self.coordinator.reconcile(
                run_id, token=command.finalize_token, sha256=command.finalize_sha256
            )

    async def close(self) -> None:
        self._closed = True
        for writer in self._writers.values():
            await writer.close()
        self._writers.clear()


def _terminal_status(status: str) -> str:
    if status in {"completed", "failed", "cancelled"}:
        return status
    return "failed"
