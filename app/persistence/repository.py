"""H2.2 业务持久化 Repository：短事务、CAS、幂等写入与终态核对。

设计原则：
1. 每个公开方法自开一个短事务，成功退出即提交，失败回滚；不在 Provider/检索
   调用期间持有事务（调用方负责这一点，Repository 内部没有任何 await 外部 IO）。
2. 终态写入与只读核对是两个独立操作：`finalize_run` 只在 finalize_state 为
   none/rolled_back 时可写；`reconcile_finalize` 永不写入。
3. 判定逻辑抽成纯函数（`decide_finalize` / `decide_reconcile` / `plan_events`），
   使九个终态分支与五个核对分支可以离线确定性测试；SQL 只负责读取与写入。
4. 数据库异常不被折叠成「记录不存在」：只有真的查询不到才返回 NOT_FOUND，
   驱动错误一律向上抛 StorageError。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.persistence.errors import StorageError
from app.persistence.models import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    ResearchEventORM,
    ResearchReportORM,
    ResearchRunORM,
)
from app.persistence.serialization import (
    FinalizeCommand,
    ReportCommand,
    canonical_json,
    sha256_hex,
)

TERMINAL = frozenset(TERMINAL_RUN_STATUSES)
ACTIVE = frozenset(ACTIVE_RUN_STATUSES)

# 允许通过 transition_run / finalize_run 的补丁键，避免任意列注入。
_PATCHABLE = frozenset(
    {
        "public_state",
        "effective_paper_ids",
        "cancel_requested",
        "event_limit_reached",
        "error",
        "metrics",
    }
)


class RepositoryError(Exception):
    """Repository 层的明确错误；API 层在 2.4 再映射为 HTTP。"""

    def __init__(self, code: str, *, run_id: str | None = None, detail: Any = None):
        super().__init__(code)
        self.code = code
        self.run_id = run_id
        self.detail = detail


class ConflictError(RepositoryError):
    """可判定的冲突（幂等键、活跃指纹、版本、事件、报告指针）。"""


class CreateOutcome(StrEnum):
    CREATED = "created"
    REPLAYED = "replayed"


class TransitionOutcome(StrEnum):
    OWNER_CONFLICT = "owner_conflict"
    UPDATED = "updated"
    VERSION_CONFLICT = "version_conflict"
    STATUS_CONFLICT = "status_conflict"
    NOT_FOUND = "not_found"


class FinalizeOutcome(StrEnum):
    OWNER_CONFLICT = "owner_conflict"
    NEW = "new"
    VERSION_CONFLICT = "version_conflict"
    EVENT_GAP_CONFLICT = "event_gap_conflict"
    REPORT_POINTER_CONFLICT = "report_pointer_conflict"
    ALREADY_COMMITTED = "already_committed"
    CONTENT_CONFLICT = "content_conflict"
    FOREIGN_TERMINAL = "foreign_terminal"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class ReconcileOutcome(StrEnum):
    CONFIRMED = "confirmed"
    CONTENT_CONFLICT = "content_conflict"
    FOREIGN_TERMINAL = "foreign_terminal"
    STILL_PENDING = "still_pending"
    UNAVAILABLE = "unavailable"


class FinalizeState(StrEnum):
    NONE = "none"
    IN_FLIGHT = "in_flight"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    UNKNOWN = "unknown"


@dataclass
class FinalizeAttempt:
    """进程内的终态尝试状态机；unknown 永久退出写路径。"""

    state: FinalizeState = FinalizeState.NONE
    token: str | None = None

    def start(self, token: str) -> None:
        if self.state not in (FinalizeState.NONE, FinalizeState.ROLLED_BACK):
            raise RepositoryError("finalize_not_permitted", detail=self.state.value)
        self.token = token
        self.state = FinalizeState.IN_FLIGHT

    def mark_committed(self) -> None:
        self._require_in_flight()
        self.state = FinalizeState.COMMITTED

    def mark_rolled_back(self) -> None:
        self._require_in_flight()
        self.state = FinalizeState.ROLLED_BACK

    def mark_unknown(self) -> None:
        self._require_in_flight()
        self.state = FinalizeState.UNKNOWN

    def _require_in_flight(self) -> None:
        if self.state is not FinalizeState.IN_FLIGHT:
            raise RepositoryError("finalize_state_invalid", detail=self.state.value)

    @property
    def can_finalize(self) -> bool:
        return self.state in (FinalizeState.NONE, FinalizeState.ROLLED_BACK)

    @property
    def must_reconcile(self) -> bool:
        return self.state is FinalizeState.UNKNOWN


@dataclass(frozen=True)
class RunState:
    """判定用最小状态快照，不携带 ORM 对象。"""

    run_id: str
    status: str
    state_version: int
    last_event_seq: int
    current_report_revision: int | None
    finalize_token: str | None = None
    finalize_sha256: str | None = None
    request_hash: str = ""
    request_payload: dict | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    mode: str
    status: str
    state_version: int
    last_event_seq: int
    current_report_revision: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: dict | None
    metrics: dict
    cancel_requested: bool
    event_limit_reached: bool
    public_state: dict
    effective_paper_ids: list
    finalize_token: str | None = None
    finalize_sha256: str | None = None
    request_hash: str = ""
    request_payload: dict | None = None
    owner_instance_id: str | None = None


@dataclass(frozen=True)
class ReportRecord:
    """已提交报告版本；后四个字段供读取侧复核内容哈希与来源。"""

    run_id: str
    revision: int
    revision_token: str
    payload_sha256: str
    public_payload: dict
    schema_version: int = 1
    snapshot_kind: str = "answer_first"
    authority_payload: dict | None = None
    source_reference: dict | None = None

    def expected_sha256(self) -> str:
        """按写入口径重算内容哈希，供调用方判定数据完整性。"""

        from app.persistence.serialization import report_content_hash

        return report_content_hash(
            schema_version=self.schema_version,
            snapshot_kind=self.snapshot_kind,
            public_payload=self.public_payload,
            authority_payload=self.authority_payload,
            source_reference=self.source_reference,
        )

    def content_matches(self) -> bool:
        try:
            return self.expected_sha256() == self.payload_sha256
        except (TypeError, ValueError):
            # 无法重算（非 JSON 可序列化等）一律视为不可复核。
            return False


@dataclass(frozen=True)
class EventCommand:
    seq: int
    node_type: str
    node_id: str
    status: str
    event_type: str
    source: str
    summary: str
    details: dict = field(default_factory=dict)
    occurred_at: datetime | None = None
    payload_sha256: str = ""

    def content(self) -> dict:
        return {
            "seq": self.seq,
            "node_type": self.node_type,
            "node_id": self.node_id,
            "status": self.status,
            "event_type": self.event_type,
            "source": self.source,
            "summary": self.summary,
            "details": self.details,
            "occurred_at": None
            if self.occurred_at is None
            else self.occurred_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def build(cls, **values) -> EventCommand:
        draft = cls(payload_sha256="", **values)
        return cls(payload_sha256=sha256_hex(draft.content()), **values)


@dataclass(frozen=True)
class CreateResult:
    outcome: CreateOutcome
    run: RunRecord | None = None


@dataclass(frozen=True)
class TransitionResult:
    outcome: TransitionOutcome
    run: RunRecord | None = None


@dataclass(frozen=True)
class AppendResult:
    appended: tuple[int, ...]
    duplicates: tuple[int, ...]
    last_event_seq: int


@dataclass(frozen=True)
class FinalizeResult:
    outcome: FinalizeOutcome
    run: RunRecord | None = None


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    run: RunRecord | None = None


# --------------------------------------------------------------------------
# 纯判定函数：不触碰数据库，可离线穷举分支
# --------------------------------------------------------------------------


def decide_finalize(
    state: RunState | None, command: FinalizeCommand
) -> FinalizeOutcome:
    """九个分支中除 UNKNOWN 外的八个；判定顺序固定，便于测试与排查。"""

    if state is None:
        return FinalizeOutcome.NOT_FOUND
    if state.terminal:
        if (
            state.finalize_token == command.finalize_token
            and state.finalize_sha256 == command.finalize_sha256
        ):
            return FinalizeOutcome.ALREADY_COMMITTED
        if state.finalize_token == command.finalize_token:
            return FinalizeOutcome.CONTENT_CONFLICT
        return FinalizeOutcome.FOREIGN_TERMINAL
    if state.state_version != command.expected_state_version:
        return FinalizeOutcome.VERSION_CONFLICT
    if state.last_event_seq != command.expected_last_event_seq:
        return FinalizeOutcome.EVENT_GAP_CONFLICT
    if state.current_report_revision != command.expected_current_revision:
        return FinalizeOutcome.REPORT_POINTER_CONFLICT
    if command.report is not None:
        expected_revision = (state.current_report_revision or 0) + 1
        if command.report.revision != expected_revision:
            return FinalizeOutcome.REPORT_POINTER_CONFLICT
    return FinalizeOutcome.NEW


def decide_reconcile(
    state: RunState | None, *, token: str, sha256: str
) -> ReconcileOutcome:
    """只读核对的四个分支；第五个 UNAVAILABLE 由 I/O 层在读取失败时给出。"""

    if state is None:
        # 终态前必有 T1 记录；缺失说明数据不一致，交由上层按未知或缺失处理。
        return ReconcileOutcome.STILL_PENDING
    if state.terminal:
        if state.finalize_token == token and state.finalize_sha256 == sha256:
            return ReconcileOutcome.CONFIRMED
        if state.finalize_token == token:
            return ReconcileOutcome.CONTENT_CONFLICT
        return ReconcileOutcome.FOREIGN_TERMINAL
    return ReconcileOutcome.STILL_PENDING


def plan_events(
    existing: dict[int, str], batch: Sequence[EventCommand]
) -> tuple[list[EventCommand], list[int]]:
    """返回（需要插入的事件，重复事件序号）；内容冲突直接抛错。"""

    to_insert: list[EventCommand] = []
    duplicates: list[int] = []
    for event in batch:
        previous = existing.get(event.seq)
        if previous is None:
            to_insert.append(event)
        elif previous == event.payload_sha256:
            duplicates.append(event.seq)
        else:
            raise ConflictError("event_content_conflict", detail=event.seq)
    return to_insert, duplicates


def decide_create_replay(existing_hash: str | None, request_hash: str) -> CreateOutcome:
    """同键已存在时的判定：同请求重放，不同请求冲突。"""

    if existing_hash is None:
        return CreateOutcome.CREATED
    if existing_hash == request_hash:
        return CreateOutcome.REPLAYED
    raise ConflictError("idempotency_key_conflict")


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _run_record(row: ResearchRunORM) -> RunRecord:
    return RunRecord(
        run_id=row.run_id,
        mode=row.mode,
        status=row.status,
        state_version=row.state_version,
        last_event_seq=row.last_event_seq,
        current_report_revision=row.current_report_revision,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        metrics=row.metrics or {},
        cancel_requested=row.cancel_requested,
        event_limit_reached=row.event_limit_reached,
        public_state=row.public_state or {},
        effective_paper_ids=list(row.effective_paper_ids or []),
        owner_instance_id=row.owner_instance_id,
        finalize_token=row.finalize_token,
        finalize_sha256=row.finalize_sha256,
        request_hash=row.request_hash,
        request_payload=row.request_payload,
    )


def _state(row: ResearchRunORM) -> RunState:
    return RunState(
        run_id=row.run_id,
        status=row.status,
        state_version=row.state_version,
        last_event_seq=row.last_event_seq,
        current_report_revision=row.current_report_revision,
        finalize_token=row.finalize_token,
        finalize_sha256=row.finalize_sha256,
        request_hash=row.request_hash,
        request_payload=row.request_payload,
    )


def _constraint_name(error: IntegrityError) -> str | None:
    """从 asyncpg 的 UniqueViolationError 取约束名，取不到返回 None。"""

    cause = getattr(error, "orig", None)
    for candidate in (cause, getattr(cause, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None


class ResearchRepository:
    """所有方法自带短事务；并发任务各自持有独立 AsyncSession。"""

    def __init__(self, resource):
        self.resource = resource

    # ---- 创建与读取 -----------------------------------------------------

    async def create_run(
        self,
        *,
        run_id: str,
        mode: str,
        request_payload: dict,
        request_hash: str,
        owner_instance_id: str,
        fingerprint: str | None = None,
        idempotency_key_hash: str | None = None,
        effective_paper_ids: Sequence[str] = (),
        public_state: dict | None = None,
        created_at: datetime | None = None,
    ) -> CreateResult:
        now = created_at or _utcnow()
        row = ResearchRunORM(
            run_id=run_id,
            mode=mode,
            status="queued",
            request_payload=request_payload,
            request_hash=request_hash,
            idempotency_key_hash=idempotency_key_hash,
            fingerprint=fingerprint,
            effective_paper_ids=list(effective_paper_ids),
            owner_instance_id=owner_instance_id,
            state_version=0,
            last_event_seq=0,
            current_report_revision=None,
            public_state=public_state or {},
            error=None,
            metrics={},
            cancel_requested=False,
            event_limit_reached=False,
            finalize_token=None,
            finalize_sha256=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
        )

        conflict: str | None = None
        record: RunRecord | None = None
        async with self.resource.session() as session:
            try:
                async with session.begin():
                    if idempotency_key_hash is not None:
                        existing = await session.scalar(
                            select(ResearchRunORM).where(
                                ResearchRunORM.idempotency_key_hash
                                == idempotency_key_hash
                            )
                        )
                        if existing is not None:
                            outcome = decide_create_replay(
                                existing.request_hash, request_hash
                            )
                            return CreateResult(outcome, _run_record(existing))
                    session.add(row)
                    await session.flush()
                    record = _run_record(row)
            except IntegrityError as exc:
                conflict = _constraint_name(exc)
                if conflict is None:
                    # 约束身份缺失时不能把失败误报为CREATED，更不能推断为活跃冲突。
                    raise ConflictError("create_conflict") from None

        if conflict is None:
            return CreateResult(CreateOutcome.CREATED, record)

        # 唯一约束是最终裁决；用新事务读取胜者，避免读到自己未提交的行。
        async with self.resource.session() as session:
            if conflict == "uq_research_runs_idempotency_key":
                winner = await session.scalar(
                    select(ResearchRunORM).where(
                        ResearchRunORM.idempotency_key_hash == idempotency_key_hash
                    )
                )
                if winner is None:
                    raise ConflictError("create_conflict", detail=conflict)
                return CreateResult(
                    decide_create_replay(winner.request_hash, request_hash),
                    _run_record(winner),
                )
            if conflict == "uq_research_runs_active_fingerprint":
                raise ConflictError("duplicate_active_fingerprint", detail=conflict)
            raise ConflictError("create_conflict", detail=conflict)

    async def list_run_history(
        self, *, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        """只读列出已提交任务的公开摘要，不加载报告或内部状态。"""
        async with self.resource.session() as session:
            total = await session.scalar(
                select(func.count()).select_from(ResearchRunORM)
            )
            query = (
                select(
                    ResearchRunORM.run_id,
                    ResearchRunORM.mode,
                    ResearchRunORM.status,
                    ResearchRunORM.created_at,
                    ResearchRunORM.request_payload["question"].astext,
                    ResearchRunORM.current_report_revision,
                )
                .order_by(
                    ResearchRunORM.created_at.desc(), ResearchRunORM.run_id.desc()
                )
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(query)).all()
            items = [
                {
                    "run_id": row[0],
                    "mode": row[1],
                    "status": row[2],
                    "created_at": row[3].isoformat() if row[3] else None,
                    "question": row[4],
                    "report_available": row[5] is not None,
                }
                for row in rows
            ]
            return items, int(total or 0)

    async def find_by_idempotency_key(self, key_hash: str) -> RunRecord | None:
        async with self.resource.session() as session:
            row = await session.scalar(
                select(ResearchRunORM).where(
                    ResearchRunORM.idempotency_key_hash == key_hash
                )
            )
            return None if row is None else _run_record(row)

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self.resource.session() as session:
            row = await session.scalar(
                select(ResearchRunORM).where(ResearchRunORM.run_id == run_id)
            )
            return None if row is None else _run_record(row)

    # ---- 状态推进 -------------------------------------------------------

    async def transition_run(
        self,
        run_id: str,
        *,
        expected_version: int,
        expected_owner_id: str | None = None,
        from_statuses: Sequence[str],
        to_status: str,
        patch: dict | None = None,
    ) -> TransitionResult:
        if to_status in TERMINAL:
            raise RepositoryError("terminal_requires_finalize", run_id=run_id)
        unknown = set(patch or {}) - _PATCHABLE
        if unknown:
            raise RepositoryError(
                "patch_not_allowed", run_id=run_id, detail=sorted(unknown)
            )
        values: dict[str, Any] = {
            "status": to_status,
            "state_version": expected_version + 1,
            "updated_at": _utcnow(),
            **(patch or {}),
        }
        async with self.resource.session() as session, session.begin():
            row = await session.scalar(
                select(ResearchRunORM)
                .where(ResearchRunORM.run_id == run_id)
                .with_for_update()
            )
            if row is None:
                return TransitionResult(TransitionOutcome.NOT_FOUND)
            if not expected_owner_id or row.owner_instance_id != expected_owner_id:
                return TransitionResult(TransitionOutcome.OWNER_CONFLICT)
            if row.state_version != expected_version:
                return TransitionResult(
                    TransitionOutcome.VERSION_CONFLICT, _run_record(row)
                )
            if row.status not in set(from_statuses) or row.status in TERMINAL:
                return TransitionResult(
                    TransitionOutcome.STATUS_CONFLICT, _run_record(row)
                )
            if to_status == "running" and row.started_at is None:
                # 同状态的选择结果CAS不能重置首次开始时间。
                values["started_at"] = _utcnow()
            for key, value in values.items():
                setattr(row, key, value)
            await session.flush()
            return TransitionResult(TransitionOutcome.UPDATED, _run_record(row))

    # ---- 事件 -----------------------------------------------------------

    async def append_events(
        self,
        run_id: str,
        events: Sequence[EventCommand],
        *,
        expected_last_seq: int,
        expected_owner_id: str | None = None,
    ) -> AppendResult:
        if not events:
            return AppendResult((), (), expected_last_seq)
        batch = sorted(events, key=lambda event: event.seq)
        seqs = [event.seq for event in batch]
        if seqs != list(range(seqs[0], seqs[0] + len(seqs))):
            raise ConflictError("event_sequence_gap", run_id=run_id)
        async with self.resource.session() as session, session.begin():
            run = await session.scalar(
                select(ResearchRunORM)
                .where(ResearchRunORM.run_id == run_id)
                .with_for_update()
            )
            if run is None:
                raise RepositoryError("run_not_found", run_id=run_id)
            if not expected_owner_id or run.owner_instance_id != expected_owner_id:
                raise ConflictError("owner_conflict", run_id=run_id)
            if run.status in TERMINAL:
                raise ConflictError("run_already_terminal", run_id=run_id)
            existing_rows = (
                await session.execute(
                    select(ResearchEventORM.seq, ResearchEventORM.payload_sha256).where(
                        ResearchEventORM.run_id == run_id,
                        ResearchEventORM.seq.in_(seqs),
                    )
                )
            ).all()
            existing = {seq: digest for seq, digest in existing_rows}
            to_insert, duplicates = plan_events(existing, batch)

            if not to_insert:
                # 整批已提交且内容一致：幂等重放，不推进高水位也不改版本。
                return AppendResult((), tuple(duplicates), run.last_event_seq)

            if run.last_event_seq != expected_last_seq:
                raise ConflictError(
                    "event_cursor_conflict",
                    run_id=run_id,
                    detail=run.last_event_seq,
                )
            if seqs[0] != expected_last_seq + 1 or duplicates:
                # 只允许「全新批次」或「整批已存在」；部分重叠说明有其他写入者。
                raise ConflictError(
                    "event_cursor_conflict", run_id=run_id, detail=seqs[0]
                )

            now = _utcnow()
            for event in to_insert:
                session.add(
                    ResearchEventORM(
                        run_id=run_id,
                        seq=event.seq,
                        node_type=event.node_type,
                        node_id=event.node_id,
                        status=event.status,
                        event_type=event.event_type,
                        source=event.source,
                        summary=event.summary,
                        details=event.details,
                        occurred_at=event.occurred_at,
                        received_at=now,
                        payload_sha256=event.payload_sha256,
                    )
                )
            highest = max(seqs)
            run.last_event_seq = highest
            run.state_version = run.state_version + 1
            run.updated_at = now
            await session.flush()
            return AppendResult(
                tuple(event.seq for event in to_insert),
                tuple(duplicates),
                highest,
            )

    async def get_events(
        self, run_id: str, *, after: int = 0, limit: int = 1000
    ) -> list[dict]:
        async with self.resource.session() as session:
            rows = (
                await session.execute(
                    select(ResearchEventORM)
                    .where(
                        ResearchEventORM.run_id == run_id,
                        ResearchEventORM.seq > after,
                    )
                    .order_by(ResearchEventORM.seq)
                    .limit(limit)
                )
            ).scalars()
            return [
                {
                    "event_id": row.seq,
                    "node_type": row.node_type,
                    "node_id": row.node_id,
                    "status": row.status,
                    "event_type": row.event_type,
                    "source": row.source,
                    "summary": row.summary,
                    "details": row.details,
                    "occurred_at": row.occurred_at,
                }
                for row in rows
            ]

    # ---- 报告版本 -------------------------------------------------------

    async def save_report_revision(
        self,
        run_id: str,
        command: ReportCommand,
        *,
        created_at: datetime | None = None,
        expected_owner_id: str | None = None,
    ) -> ReportRecord:
        now = created_at or _utcnow()
        async with self.resource.session() as session, session.begin():
            run = await session.scalar(
                select(ResearchRunORM)
                .where(ResearchRunORM.run_id == run_id)
                .with_for_update()
            )
            if run is None:
                raise RepositoryError("run_not_found", run_id=run_id)
            if not expected_owner_id or run.owner_instance_id != expected_owner_id:
                raise ConflictError("owner_conflict", run_id=run_id)
            if run.status in TERMINAL:
                raise ConflictError("run_already_terminal", run_id=run_id)
            if run.current_report_revision != command.expected_current_revision:
                raise ConflictError(
                    "report_pointer_conflict",
                    run_id=run_id,
                    detail=run.current_report_revision,
                )

            existing = await session.scalar(
                select(ResearchReportORM).where(
                    ResearchReportORM.run_id == run_id,
                    ResearchReportORM.revision == command.revision,
                )
            )
            if existing is not None:
                if (
                    existing.revision_token == command.revision_token
                    and existing.payload_sha256 == command.payload_sha256
                ):
                    return self._report_record(existing)
                raise ConflictError("report_content_conflict", run_id=run_id)

            row = ResearchReportORM(
                run_id=run_id,
                revision=command.revision,
                revision_token=command.revision_token,
                schema_version=command.schema_version,
                snapshot_kind=command.snapshot_kind,
                public_payload=command.public_payload,
                payload_sha256=command.payload_sha256,
                authority_payload=command.authority_payload,
                source_reference=command.source_reference,
                created_at=now,
            )
            session.add(row)
            run.current_report_revision = command.revision
            run.state_version = run.state_version + 1
            run.updated_at = now
            await session.flush()
            return self._report_record(row)

    async def get_current_report(self, run_id: str) -> ReportRecord | None:
        async with self.resource.session() as session:
            run = await session.scalar(
                select(ResearchRunORM).where(ResearchRunORM.run_id == run_id)
            )
            if run is None or run.current_report_revision is None:
                return None
            report = await session.scalar(
                select(ResearchReportORM).where(
                    ResearchReportORM.run_id == run_id,
                    ResearchReportORM.revision == run.current_report_revision,
                )
            )
            return None if report is None else self._report_record(report)

    # ---- 终态写入与只读核对 ---------------------------------------------

    async def finalize_run(
        self,
        command: FinalizeCommand,
        *,
        trailing_events: Sequence[EventCommand] = (),
        expected_owner_id: str | None = None,
    ) -> FinalizeResult:
        try:
            return await self._finalize_once(
                command, trailing_events, expected_owner_id
            )
        except StorageError:
            # 存储层无法确认事务是否已提交；按定稿契约一律视为 unknown，
            # 由调用方转入只读核对，绝不在这里补写。
            return FinalizeResult(FinalizeOutcome.UNKNOWN)

    async def _finalize_once(
        self,
        command: FinalizeCommand,
        trailing_events: Sequence[EventCommand],
        expected_owner_id: str | None,
    ) -> FinalizeResult:
        async with self.resource.session() as session, session.begin():
            run = await session.scalar(
                select(ResearchRunORM)
                .where(ResearchRunORM.run_id == command.run_id)
                .with_for_update()
            )
            if run is not None and (
                not expected_owner_id or run.owner_instance_id != expected_owner_id
            ):
                return FinalizeResult(FinalizeOutcome.OWNER_CONFLICT)
            outcome = decide_finalize(None if run is None else _state(run), command)
            if outcome is FinalizeOutcome.ALREADY_COMMITTED:
                # 原终态哈希未覆盖尾事件内容；跨实例重放时必须核对已提交事件。
                tail = sorted(trailing_events, key=lambda item: item.seq)
                expected = list(
                    range(
                        command.expected_last_event_seq + 1,
                        command.final_last_event_seq + 1,
                    )
                )
                if [event.seq for event in tail] != expected:
                    return FinalizeResult(
                        FinalizeOutcome.CONTENT_CONFLICT, _run_record(run)
                    )
                rows = (
                    await session.execute(
                        select(
                            ResearchEventORM.seq, ResearchEventORM.payload_sha256
                        ).where(
                            ResearchEventORM.run_id == command.run_id,
                            ResearchEventORM.seq.in_(expected),
                        )
                    )
                ).all()
                persisted = {seq: digest for seq, digest in rows}
                if len(persisted) != len(tail) or any(
                    persisted.get(event.seq) != sha256_hex(event.content())
                    for event in tail
                ):
                    return FinalizeResult(
                        FinalizeOutcome.CONTENT_CONFLICT, _run_record(run)
                    )
                return FinalizeResult(outcome, _run_record(run))
            if outcome is not FinalizeOutcome.NEW:
                return FinalizeResult(
                    outcome, None if run is None else _run_record(run)
                )

            # 尾部事件必须补齐 (expected_last_event_seq, final_last_event_seq]；
            # 缺口或越界都在写库前拒绝，绝不能静默推进高水位。
            tail = sorted(trailing_events, key=lambda item: item.seq)
            expected_range = list(
                range(
                    command.expected_last_event_seq + 1,
                    command.final_last_event_seq + 1,
                )
            )
            if [event.seq for event in tail] != expected_range:
                raise ConflictError("finalize_event_gap", run_id=command.run_id)

            now = _utcnow()
            if command.report is not None:
                session.add(
                    ResearchReportORM(
                        run_id=command.run_id,
                        revision=command.report.revision,
                        revision_token=command.report.revision_token,
                        schema_version=command.report.schema_version,
                        snapshot_kind=command.report.snapshot_kind,
                        public_payload=command.report.public_payload,
                        payload_sha256=command.report.payload_sha256,
                        authority_payload=command.report.authority_payload,
                        source_reference=command.report.source_reference,
                        created_at=now,
                    )
                )
                run.current_report_revision = command.report.revision

            for event in tail:
                session.add(
                    ResearchEventORM(
                        run_id=command.run_id,
                        seq=event.seq,
                        node_type=event.node_type,
                        node_id=event.node_id,
                        status=event.status,
                        event_type=event.event_type,
                        source=event.source,
                        summary=event.summary,
                        details=event.details,
                        occurred_at=event.occurred_at,
                        received_at=now,
                        payload_sha256=event.payload_sha256,
                    )
                )

            run.status = command.status
            run.finished_at = datetime.fromisoformat(command.finished_at)
            run.error = command.error
            run.metrics = command.metrics
            run.last_event_seq = command.final_last_event_seq
            run.finalize_token = command.finalize_token
            run.finalize_sha256 = command.finalize_sha256
            run.state_version = run.state_version + 1
            run.updated_at = now
            await session.flush()
            return FinalizeResult(FinalizeOutcome.NEW, _run_record(run))

    async def reconcile_finalize(
        self, run_id: str, *, token: str, sha256: str
    ) -> ReconcileResult:
        """只读核对：单次查询，不加锁、不写任何行。"""

        try:
            async with self.resource.session() as session:
                run = await session.scalar(
                    select(ResearchRunORM).where(ResearchRunORM.run_id == run_id)
                )
                # 必须在 session 关闭前取值：关闭时的 rollback 会过期所有实例属性。
                outcome = decide_reconcile(
                    None if run is None else _state(run), token=token, sha256=sha256
                )
                record = None if run is None else _run_record(run)
        except StorageError:
            return ReconcileResult(ReconcileOutcome.UNAVAILABLE)
        return ReconcileResult(outcome, record)

    async def verify_trailing_events(
        self, run_id: str, expected: Sequence[tuple[int, str]]
    ) -> bool | None:
        """只读核对尾事件；None 表示数据库不可用，不能解除锁存。"""
        try:
            async with self.resource.session() as session:
                rows = (
                    await session.execute(
                        select(
                            ResearchEventORM.seq, ResearchEventORM.payload_sha256
                        ).where(
                            ResearchEventORM.run_id == run_id,
                            ResearchEventORM.seq.in_([seq for seq, _ in expected]),
                        )
                    )
                ).all()
                return len(rows) == len(expected) and dict(rows) == dict(expected)
        except StorageError:
            return None

    # ---- 内部 -----------------------------------------------------------

    @staticmethod
    def _report_record(row: ResearchReportORM) -> ReportRecord:
        return ReportRecord(
            run_id=row.run_id,
            revision=row.revision,
            revision_token=row.revision_token,
            payload_sha256=row.payload_sha256,
            public_payload=row.public_payload,
            schema_version=row.schema_version,
            snapshot_kind=row.snapshot_kind,
            authority_payload=row.authority_payload,
            source_reference=row.source_reference,
        )


def event_hash(event: EventCommand) -> str:
    """供测试与迁移核对使用：事件内容哈希口径。"""

    return sha256_hex(event.content())


def canonical_request(request_payload: dict) -> str:
    """请求哈希：覆盖全部已验证字段与默认值。"""

    return sha256_hex(request_payload)


__all__ = [
    "ACTIVE",
    "TERMINAL",
    "AppendResult",
    "ConflictError",
    "CreateOutcome",
    "CreateResult",
    "EventCommand",
    "FinalizeAttempt",
    "FinalizeOutcome",
    "FinalizeResult",
    "FinalizeState",
    "ReconcileOutcome",
    "ReconcileResult",
    "ReportRecord",
    "RepositoryError",
    "ResearchRepository",
    "RunRecord",
    "RunState",
    "TransitionOutcome",
    "TransitionResult",
    "canonical_json",
    "canonical_request",
    "decide_create_replay",
    "decide_finalize",
    "decide_reconcile",
    "event_hash",
    "plan_events",
]
