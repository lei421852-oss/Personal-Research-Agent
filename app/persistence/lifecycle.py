"""Manager 与持久化组件的适配层：创建、running、事件与终态命令。

本模块只做「快照 → 不可变命令」的映射，不改变研究执行顺序或对外 DTO。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4
from weakref import WeakValueDictionary

from app.persistence.repository import (
    ConflictError,
    EventCommand,
    FinalizeOutcome,
    ReconcileOutcome,
    RepositoryError,
    ResearchRepository,
    RunRecord,
    TransitionOutcome,
)
from app.persistence.serialization import (
    build_finalize_command,
    build_report_command,
    sha256_hex,
)
from app.persistence.writer import AppendEvents, RunWriter, SavePaperSelection


@dataclass(frozen=True)
class StartResult:
    record: RunRecord
    replayed: bool


def key_hash(raw_key: str) -> str:
    return sha256_hex({"idempotency_key": raw_key})


def request_hash(payload: dict) -> str:
    return sha256_hex(payload)


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
        self._active_run_ids: set[str] = set()
        self._claim_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._closed = False

    # ---- 生命周期 -------------------------------------------------------

    async def start(
        self,
        *,
        run_id: str,
        payload: dict,
        fingerprint: str | None,
        created_at: datetime | None = None,
        idempotency_key_hash: str | None = None,
    ) -> StartResult:
        """创建 queued 记录并启动该 run 的 Writer；失败即拒绝调度。"""

        result = await self.repository.create_run(
            run_id=run_id,
            mode="live",
            request_payload=payload,
            request_hash=sha256_hex(payload),
            owner_instance_id=self.owner_instance_id,
            fingerprint=fingerprint,
            idempotency_key_hash=idempotency_key_hash,
            created_at=created_at,
        )
        writer = (
            self._writer_factory(run_id)
            if self._writer_factory is not None
            else RunWriter(
                self.repository,
                latch=self.latch,
                owner_instance_id=self.owner_instance_id,
            )
        )
        if result.outcome.value == "replayed":
            # 并发同键创建：本实例不是胜方，绝不启动第二个 Writer 或 Task。
            return StartResult(record=result.run, replayed=True)
        writer.start()
        self._writers[run_id] = writer
        return StartResult(record=result.run, replayed=False)

    async def find_by_key(self, key_digest: str):
        return await self.repository.find_by_idempotency_key(key_digest)

    # ---- 只读查询 -------------------------------------------------------
    #
    # 三个 GET 的权威数据源。全部只读：不建记录、不推进状态、不启动 Task。
    # 存储层故障由 DatabaseResource 转成 StorageError，调用方据此返回 503，
    # 不回退到内存缓存。

    async def get_run(self, run_id: str):
        return await self.repository.get_run(run_id)

    async def get_events(self, run_id: str, *, after: int = 0):
        return await self.repository.get_events(run_id, after=after)

    async def get_current_report(self, run_id: str):
        return await self.repository.get_current_report(run_id)

    async def mark_running(self, run_id: str) -> TransitionOutcome:
        # 事务结果和本地登记之间不能被同一 lifecycle 的 discard 打断。
        async with self._claim_locks.setdefault(run_id, asyncio.Lock()):
            return await self._mark_running_locked(run_id)

    async def _mark_running_locked(self, run_id: str) -> TransitionOutcome:
        record = await self.repository.get_run(run_id)
        if record is None:
            return TransitionOutcome.NOT_FOUND
        if record.owner_instance_id != self.owner_instance_id:
            return TransitionOutcome.OWNER_CONFLICT
        if record.status != "queued":
            return TransitionOutcome.STATUS_CONFLICT
        result = await self.repository.transition_run(
            run_id,
            expected_version=record.state_version,
            expected_owner_id=self.owner_instance_id,
            from_statuses=("queued",),
            to_status="running",
        )
        if result.outcome is TransitionOutcome.UPDATED:
            self._active_run_ids.add(run_id)
        return result.outcome

    async def discard(self, run_id: str) -> None:
        """无执行资格时只释放本地 Writer，不写数据库终态。"""
        async with self._claim_locks.setdefault(run_id, asyncio.Lock()):
            if run_id in self._active_run_ids:
                return
            writer = self._writers.pop(run_id, None)
            if writer is not None:
                await writer.close()

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
        # 只有真实选择完成，或已进入持有明确范围的Planner时，才能确认实际范围。
        selected = None
        if (
            event.node_type == "PaperSelection"
            and event.event_type == "completed"
            and event.status == "completed"
        ):
            selected = event.details.get("selected_paper_ids")
        elif (
            event.node_type == "Planner"
            and event.event_type == "started"
            and event.status == "running"
        ):
            selected = event.details.get("paper_ids")
        if (
            isinstance(selected, list)
            and selected
            and all(isinstance(item, str) and item.strip() for item in selected)
        ):
            # 与事件使用同一个串行队列；终态flush同时等待选择结果提交。
            writer.submit(
                SavePaperSelection(
                    run_id=run_id,
                    paper_ids=tuple(dict.fromkeys(item.strip() for item in selected)),
                )
            )

    async def finish(self, run_id: str, snapshot: dict) -> None:
        """等待屏障后用协调器提交终态；未知结果只做只读核对。"""

        # 未取得执行资格的取消，只能收尾仍在排队的记录。
        # 后续终态 CAS 使用此状态版本，防止检查后被其他执行者领取。
        if snapshot.get("finalize_requires_queued"):
            queued = await self.repository.get_run(run_id)
            if queued is None or queued.owner_instance_id != self.owner_instance_id:
                raise ConflictError("owner_conflict", run_id=run_id)
            if queued.status != "queued":
                raise ConflictError("unclaimed_run_not_queued", run_id=run_id)

        writer = self._writers.get(run_id)
        if writer is not None:
            await writer.flush()
        record = await self.repository.get_run(run_id)
        if record is None:
            raise RuntimeError("run_not_found")
        if record.owner_instance_id != self.owner_instance_id:
            raise ConflictError("owner_conflict", run_id=run_id)
        if snapshot.get("finalize_requires_queued") and (
            record.status != "queued" or record.state_version != queued.state_version
        ):
            raise ConflictError("unclaimed_run_not_queued", run_id=run_id)

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
            result = await self.repository.finalize_run(
                command, expected_owner_id=self.owner_instance_id
            )
            outcome = result.outcome
        else:
            outcome = await self.coordinator.finalize(
                command, expected_owner_id=self.owner_instance_id
            )
        if outcome is FinalizeOutcome.UNKNOWN:
            if self.coordinator is None:
                raise RepositoryError("finalize_outcome_unknown", run_id=run_id)
            reconciled = await self.coordinator.reconcile(
                run_id, token=command.finalize_token, sha256=command.finalize_sha256
            )
            if reconciled is not ReconcileOutcome.CONFIRMED:
                raise RepositoryError("finalize_outcome_unknown", run_id=run_id)
        elif outcome not in {FinalizeOutcome.NEW, FinalizeOutcome.ALREADY_COMMITTED}:
            raise ConflictError(f"finalize_{outcome.value}", run_id=run_id)
        self._active_run_ids.discard(run_id)

    async def close(self) -> None:
        self._closed = True
        for writer in self._writers.values():
            await writer.close()
        self._writers.clear()
        self._active_run_ids.clear()


def _terminal_status(status: str) -> str:
    if status in {"completed", "failed", "cancelled"}:
        return status
    return "failed"
