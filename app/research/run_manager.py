"""单进程研究任务管理：后台执行、限流和取消，不参与研究决策。"""

import asyncio
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.research.run_events import RunEvent, sanitize_public
from app.research.run_presenter import execution_events, present_execution
from app.research.run_replay import ReplayError
from app.research.scheduling import AdmissionRegistry, SchedulingFailure

LOGGER = logging.getLogger(__name__)

TERMINAL = frozenset({"completed", "failed", "cancelled"})

# admission 失败码表：与阶段 2.0 §4.5 的错误映射逐条对应。
_SUBMISSION_UNKNOWN = SchedulingFailure(
    code="submission_outcome_unknown",
    message="任务提交结果无法确认，未启动研究执行；请用同一幂等键只做查询核对。",
    status_code=503,
    retry_mode="reconcile_only",
)
_SCHEDULING_UNKNOWN = SchedulingFailure(
    code="task_scheduling_outcome_unknown",
    message="任务已提交但调度结果尚未确定；请用同一幂等键只做查询核对。",
    status_code=503,
    retry_mode="reconcile_only",
)
_SCHEDULING_FAILED = SchedulingFailure(
    code="task_scheduling_failed",
    message="任务已保存但未能安排执行，已按失败收尾；可用新的幂等键重试。",
    status_code=500,
    retry_mode="safe_retry",
)
_CLOSEOUT_INCOMPLETE = SchedulingFailure(
    code="run_persistence_incomplete",
    message="调度失败后的收尾未能确认落库；请先核对该任务再重试。",
    status_code=503,
    retry_mode="reconcile_only",
)


class ResearchRunRequest(BaseModel):
    """只接受研究问题、已准备论文身份和有界调用预算。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    question: Annotated[StrictStr, Field(min_length=1, max_length=4000, pattern=r"\S")]
    paper_ids: list[Annotated[StrictStr, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=3
    )
    language_mode: Literal["auto", "original"] = "auto"
    max_model_calls: Annotated[StrictInt, Field(ge=1, le=200)] = 200

    @model_validator(mode="after")
    def unique_papers(self):
        if len(self.paper_ids) == 1:
            raise ValueError("请选择2–3篇论文，或不选择以进入自动模式。")
        if len(self.paper_ids) != len(set(self.paper_ids)):
            raise ValueError("paper_ids must be unique")
        return self


class MainAgentRunRequest(ResearchRunRequest):
    """主Agent入口接受问题；API可选范围允许1–3篇，界面不要求选择。"""

    @model_validator(mode="after")
    def unique_papers(self):
        if len(self.paper_ids) != len(set(self.paper_ids)):
            raise ValueError("paper_ids must be unique")
        return self


class RunManagerError(Exception):
    def __init__(
        self,
        code,
        message,
        status_code=409,
        *,
        retry_mode: str | None = None,
        run_id: str | None = None,
    ):
        super().__init__(code)
        self.code, self.message, self.status_code = code, message, status_code
        # 只在能给出确定指引时携带：调用方据此区分「可原样重试」与「只能核对」。
        self.retry_mode = retry_mode
        self.run_id = run_id


@dataclass
class _Run:
    run_id: str
    mode: str
    question: str
    paper_ids: list[str]
    created_at: str
    fingerprint: str | None = None
    status: str = "queued"
    cancel_requested: bool = False
    events: list[RunEvent] = field(default_factory=list)
    task: asyncio.Task | None = None
    execution: dict | None = None
    report: dict | None = None
    error: dict | None = None
    finished_at: float | None = None
    event_limit_reached: bool = False
    research_question: str | None = None
    query_language: dict = field(default_factory=dict)
    paper_selection: dict = field(default_factory=dict)
    # 持久化生命周期：ingress 屏障、迟到事件、受管理收尾任务。
    ingress_closed: bool = False
    pending_deliveries: int = 0
    delivery_event: asyncio.Event | None = None
    late_events: int = 0
    finalize_task: asyncio.Task | None = None
    persisted: bool = False
    persistence_error: str | None = None
    delivery_lock: threading.Lock = field(default_factory=threading.Lock)


class ResearchRunManager:
    """一个进程内运行一个真实研究；队列与历史都有上限。"""

    def __init__(
        self,
        runner,
        dependencies,
        replay_repository,
        *,
        max_concurrent_runs=1,
        max_queued_runs=2,
        max_retained_runs=20,
        history_ttl_seconds=3600,
        timeout_seconds=420,
        max_events=1000,
        lifecycle=None,
        finalize_timeout=10.0,
        admission_timeout=5.0,
        scheduler=None,
    ):
        if (
            max_concurrent_runs < 1
            or max_queued_runs < 0
            or max_retained_runs < max_concurrent_runs + max_queued_runs
            or history_ttl_seconds <= 0
            or timeout_seconds <= 0
            or max_events < 1
            or admission_timeout <= 0
        ):
            raise ValueError("Invalid run manager limits")
        self.runner = runner
        self.dependencies = dependencies
        self.replays = replay_repository
        self.max_concurrent_runs = max_concurrent_runs
        self.max_queued_runs = max_queued_runs
        self.max_retained_runs = max_retained_runs
        self.history_ttl_seconds = history_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.max_events = max_events
        self.lifecycle = lifecycle
        self.finalize_timeout = finalize_timeout
        self.admission_timeout = admission_timeout
        # 调度入口可注入：默认 asyncio.create_task，测试用确定性替身覆盖。
        self._scheduler = scheduler
        self._admissions = AdmissionRegistry(timeout=admission_timeout)
        self._finalize_tasks: dict[str, asyncio.Task] = {}
        self._slots = asyncio.Semaphore(max_concurrent_runs)
        self._runs: dict[str, _Run] = {}
        self._closed = False

    def _prune(self):
        now = time.monotonic()
        for identity, run in list(self._runs.items()):
            if (
                run.finished_at is not None
                and now - run.finished_at > self.history_ttl_seconds
            ):
                del self._runs[identity]
        # 只淘汰终态任务；运行中及排队任务不会为了腾空间被丢弃。
        terminal = sorted(
            (r for r in self._runs.values() if r.finished_at is not None),
            key=lambda r: r.finished_at,
        )
        while len(self._runs) >= self.max_retained_runs and terminal:
            del self._runs[terminal.pop(0).run_id]
        # admission 登记不得比它所属的 run 活得更久；已失败登记单独按上限保留。
        self._admissions.retain(set(self._runs))

    @staticmethod
    def _fingerprint(request):
        payload = {
            "question": " ".join(request.question.casefold().split()),
            "papers": sorted(request.paper_ids),
            "language_mode": request.language_mode,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _get(self, identity):
        run = self._runs.get(identity)
        if run is None:
            raise RunManagerError("run_not_found", "任务不存在或已过保留期限。", 404)
        if (
            run.finished_at is not None
            and time.monotonic() - run.finished_at > self.history_ttl_seconds
        ):
            del self._runs[identity]
            raise RunManagerError("run_not_found", "任务不存在或已过保留期限。", 404)
        return run

    def _append(self, run, **event):
        if len(run.events) >= self.max_events:
            run.event_limit_reached = True
            return
        # 不为派生/回放状态自动生成发生时间；只记录真实回调的观察时间。
        source = event.get("source", "live")
        event.setdefault("source", source)
        event.setdefault(
            "timestamp", datetime.now(UTC).isoformat() if source == "live" else None
        )
        recorded = RunEvent(
            run_id=run.run_id,
            event_id=len(run.events) + 1,
            **event,
        )
        run.events.append(recorded)
        return recorded

    def _sink(self, run):
        loop = asyncio.get_running_loop()

        def emit(**event):
            # 关闭接收后到达的回调是迟到事件：计数但不再进入报告/事件流。
            if run.ingress_closed:
                with run.delivery_lock:
                    run.late_events += 1
                return
            with run.delivery_lock:
                run.pending_deliveries += 1

            # Provider/检索由旧适配器在线程中执行；所有事件序号在主循环串行分配。
            def deliver():
                try:
                    self._deliver(run, event)
                finally:
                    with run.delivery_lock:
                        run.pending_deliveries -= 1
                        if (
                            run.pending_deliveries == 0
                            and run.delivery_event is not None
                        ):
                            run.delivery_event.set()

            loop.call_soon_threadsafe(deliver)

        return emit

    def _deliver(self, run, event):
        if run.status not in TERMINAL:
            # 原报告已生成时立即可查看，翻译不阻塞原文读取。
            snapshot = event.pop("report_snapshot", None)
            if isinstance(snapshot, dict):
                run.report = sanitize_public(snapshot)
            details = event.get("details", {})
            if (
                event.get("node_type") == "QueryLanguage"
                and event.get("event_type") == "completed"
            ):
                run.query_language = sanitize_public(details)
                run.research_question = details.get("research_question")
            if (
                event.get("node_type") == "PaperSelection"
                and event.get("event_type") == "completed"
            ):
                run.paper_selection = sanitize_public(details)
                run.paper_ids = list(details.get("selected_paper_ids", []))
            recorded = self._append(run, **event)
            if recorded is not None and self.lifecycle is not None:
                self.lifecycle.on_event(run.run_id, recorded)

    async def create(
        self,
        request: ResearchRunRequest,
        *,
        idempotency_key: str | None = None,
        replay_only: bool = False,
    ):
        if self._closed:
            raise RunManagerError(
                "service_stopping", "服务正在停止，不能启动新任务。", 503
            )
        self._prune()
        payload = {
            "question": request.question,
            "paper_ids": list(request.paper_ids),
            "language_mode": request.language_mode,
            "max_model_calls": request.max_model_calls,
        }
        key_digest = None
        request_digest = None
        if self.lifecycle is not None:
            from app.persistence.lifecycle import key_hash, request_hash

            request_digest = request_hash(payload)
            if idempotency_key:
                key_digest = key_hash(idempotency_key)
                existing = await self.lifecycle.find_by_key(key_digest)
                if existing is not None:
                    if existing.request_hash != request_digest:
                        raise RunManagerError(
                            "idempotency_key_conflict",
                            "同一幂等键已用于不同的请求内容。",
                            409,
                        )
                    existing = await self._settle_replay(existing, key_digest)
                    return self._persisted_status(existing, replayed=True)
                if replay_only:
                    raise RunManagerError(
                        "submission_not_found",
                        "没有找到该幂等键对应的已提交记录。",
                        404,
                    )
        if replay_only:
            raise RunManagerError(
                "submission_not_found", "没有找到该幂等键对应的已提交记录。", 404
            )
        is_main = getattr(self.runner, "main_agent_entry", False)
        if not is_main and len(request.paper_ids) == 1:
            raise RunManagerError("invalid_papers", "旧研究入口要求2–3篇论文。", 422)
        try:
            if is_main:
                from app.papers.catalog import load_paper_catalog

                known = set(load_paper_catalog().documents)
            else:
                known = {paper["paper_id"] for paper in self.dependencies.papers()}
        except Exception:  # noqa: BLE001 - 语料失败使用稳定公开错误，不回显内部路径
            raise RunManagerError(
                "research_unavailable", "预处理论文语料不可用；请查看健康状态。", 503
            ) from None
        if any(identity not in known for identity in request.paper_ids):
            raise RunManagerError(
                "invalid_papers", "只能选择当前已准备正文证据的论文。", 422
            )
        fingerprint = self._fingerprint(request)
        active = [
            r
            for r in self._runs.values()
            if r.mode == "live" and self._occupies_capacity(r)
        ]
        if any(r.fingerprint == fingerprint for r in active):
            raise RunManagerError(
                "duplicate_run", "相同问题与论文的任务仍在执行，请查看已有任务。", 409
            )
        if len(active) >= self.max_concurrent_runs + self.max_queued_runs:
            raise RunManagerError("run_capacity", "研究运行及排队数量已达上限。", 429)
        health = self.dependencies.health()
        ready = (
            health.get("main_agent_ready", health.get("real_research_ready", False))
            if is_main
            else health["real_research_ready"]
        )
        if not ready:
            raise RunManagerError(
                "research_unavailable",
                "真实研究依赖尚未就绪；可以查看健康状态或历史回放。",
                503,
            )
        if health.get("language_adapter_ready") is False:
            raise RunManagerError(
                "language_unavailable",
                "语言适配服务配置未就绪；仍可查看原始历史回放。",
                503,
            )
        run = _Run(
            uuid4().hex,
            "live",
            request.question,
            list(request.paper_ids),
            datetime.now(UTC).isoformat(),
            fingerprint,
        )
        self._admissions.begin(run.run_id)
        self._runs[run.run_id] = run
        if self.lifecycle is not None:
            # 创建记录必须先可靠提交，才有首次调度资格。
            try:
                started = await self.lifecycle.start(
                    run_id=run.run_id,
                    payload=payload,
                    fingerprint=fingerprint,
                    created_at=datetime.now(UTC),
                    idempotency_key_hash=key_digest,
                )
            except Exception as exc:
                from app.persistence.repository import ConflictError

                if (
                    isinstance(exc, ConflictError)
                    and exc.code == "duplicate_active_fingerprint"
                    and exc.detail == "uq_research_runs_active_fingerprint"
                ):
                    # 数据库已明确拒绝第二条记录；撤销本次候选，不继承调度资格。
                    self._admissions.discard(run.run_id)
                    self._runs.pop(run.run_id, None)
                    raise RunManagerError(
                        "duplicate_run",
                        "相同问题与论文的任务仍在执行，请查看已有任务。",
                        409,
                    ) from None
                LOGGER.warning("run persistence start failed", exc_info=True)
                # 提交结果无法确认：登记失败，让同键重放方立刻拿到同一判定而不是等超时。
                self._admissions.mark_failed(run.run_id, _SUBMISSION_UNKNOWN)
                self._runs.pop(run.run_id, None)
                raise self._failure_error(_SUBMISSION_UNKNOWN, run.run_id) from None
            if getattr(started, "replayed", False):
                # 并发同键的两个请求里本实例不是胜方：记录归胜方，绝不另起 Task。
                self._runs.pop(run.run_id, None)
                self._admissions.discard(run.run_id)
                return self._persisted_status(started.record, replayed=True)
        try:
            task = self._schedule(run, request)
        except Exception:  # 调度失败必须转成稳定公开错误
            LOGGER.warning("run scheduling failed", exc_info=True)
            raise await self._abandon_scheduling(run) from None
        run.task = task
        self._admissions.mark_registered(run.run_id, task)
        task.add_done_callback(lambda done: self._finished(run, done))
        return self.status(run.run_id)

    def _schedule(self, run, request):
        """创建 Task；失败时关闭未运行的协程，不留下未 await 告警。"""

        pending = self._execute(run, request)
        try:
            if self._scheduler is not None:
                return self._scheduler(pending, name=f"research:{run.run_id}")
            return asyncio.create_task(pending, name=f"research:{run.run_id}")
        except BaseException:
            pending.close()
            raise

    async def _abandon_scheduling(self, run) -> RunManagerError:
        """已确认未安排：先提交失败收尾，再按收尾结果决定对外错误码。"""

        run.status = "failed"
        run.error = {
            "code": "task_scheduling_failed",
            "message": "任务已保存但未能安排执行；未调用任何研究组件。",
        }
        run.finished_at = time.monotonic()
        try:
            await asyncio.wait_for(
                self._finish_persistently(run), self.finalize_timeout
            )
        except Exception:  # 收尾失败只暴露稳定错误码
            LOGGER.warning("scheduling failure close-out failed", exc_info=True)
            self._admissions.mark_failed(run.run_id, _CLOSEOUT_INCOMPLETE)
            return self._failure_error(_CLOSEOUT_INCOMPLETE, run.run_id)
        self._admissions.mark_failed(run.run_id, _SCHEDULING_FAILED)
        return self._failure_error(_SCHEDULING_FAILED, run.run_id)

    async def _settle_replay(self, existing, key_digest):
        """重放前确认归属：本实例正在提交的 run 必须先在界内落定。"""

        if existing.status in TERMINAL:
            return existing
        operation = self._admissions.peek(existing.run_id)
        if operation is not None:
            if not operation.settled:
                # 数据库可能已提交、Task 尚未注册：等待胜方落定，不提前 409、不建第二个 Task。
                operation = await self._admissions.settled(
                    existing.run_id, timeout=self.admission_timeout
                )
                if operation is None:
                    raise self._failure_error(_SCHEDULING_UNKNOWN, existing.run_id)
            if operation.failure is not None:
                # 本实例已对同一 run 作出判定：重放方拿同一结论，不另判一次。
                raise self._failure_error(operation.failure, existing.run_id)
            refreshed = await self.lifecycle.find_by_key(key_digest)
            existing = refreshed or existing
        if existing.status not in TERMINAL and not self._is_attached(existing.run_id):
            raise RunManagerError(
                "run_not_attached",
                "该任务由其他实例持有或已失去归属，不能在此重放。",
                409,
                run_id=existing.run_id,
            )
        return existing

    @staticmethod
    def _failure_error(failure: SchedulingFailure, run_id: str) -> RunManagerError:
        return RunManagerError(
            failure.code,
            failure.message,
            failure.status_code,
            retry_mode=failure.retry_mode,
            run_id=run_id,
        )

    def _attach(self, run, execution, *, source="derived"):
        run.execution = execution
        run.research_question = execution.get(
            "research_question", run.research_question
        )
        run.query_language = sanitize_public(
            execution.get("query_language", run.query_language)
        )
        run.paper_selection = sanitize_public(
            execution.get("paper_selection", run.paper_selection)
        )
        if execution.get("paper_ids"):
            run.paper_ids = list(execution["paper_ids"])
        if execution.get("entry_kind") == "react":
            # 主入口已有实时角色事件；不能从旧报告结构推定未执行的角色。
            run.report = sanitize_public(execution["answer_first"])
            return
        for event in execution_events(execution, source=source):
            self._append(run, **event)
        run.report = present_execution(execution)
        language = execution.get("report_language")
        if isinstance(language, dict):
            public_language = sanitize_public(
                {
                    key: value
                    for key, value in language.items()
                    if key != "display_report"
                }
            )
            chinese_report = None
            if language.get("status") == "translated":
                from app.research.report_language import (
                    ReportLanguageAdapter,
                    readable_report_fields,
                )

                # 只把已校验的可读字段合并到终态 DTO，不能用翻译前快照覆盖最终成本或诊断。
                merged = ReportLanguageAdapter().apply_translations(
                    run.report,
                    readable_report_fields(language.get("display_report", {})),
                    source=language.get("translation_source", "model"),
                    model_calls=0,
                )
                if merged.status == "translated":
                    chinese_report = sanitize_public(merged.display_report)
                    public_language["final_source_sha256"] = merged.source_sha256
                else:
                    public_language.update(
                        status="failed", issue_code="final_report_translation_mismatch"
                    )
            run.report["language"] = public_language
            run.report["chinese_report"] = chinese_report

    async def _execute(self, run, request):
        try:
            async with self._slots:
                if self.lifecycle is not None:
                    # 先持久化 running，成功后才调用 Provider/Runner。
                    await self.lifecycle.mark_running(run.run_id)
                run.status = "running"
                execution = await asyncio.wait_for(
                    self.runner.run(request, self._sink(run)),
                    timeout=self.timeout_seconds,
                )
                # 消费已投递的真实回调，再追加根据完整结果推导的阶段终态。
                await asyncio.sleep(0)
                self._attach(run, execution)
                research = execution.get("research", execution)
                run.status = (
                    "completed" if research.get("status") == "completed" else "failed"
                )
                if run.status == "failed":
                    issue_type = research.get("issue_type")
                    known_issues = {
                        "no_matching_prepared_papers": "当前已准备论文无法满足这项研究，未使用固定默认论文组合。",
                        "paper_selection_failed": "自动论文选择未返回合法可研究范围；可以手动选择2–3篇论文后重试。",
                        "query_language_failed": "问题语言转换失败，未提交错误译文；可以选择原文直通模式后重试。",
                        "prepared_paper_unavailable": "所选论文缺少可检索的已准备正文证据。",
                        "model_budget_exhausted": "研究模型调用预算已耗尽，已有真实结果仍保留。",
                    }
                    run.error = {
                        "code": issue_type
                        if issue_type in known_issues
                        else "research_failed",
                        "message": known_issues.get(
                            issue_type, "研究执行失败；已获得的记录和报告仍可查看。"
                        ),
                    }
        except asyncio.CancelledError as exc:
            snapshot = getattr(exc, "evaluation_snapshot", None) or getattr(
                exc, "partial_execution", None
            )
            if snapshot:
                self._attach(run, snapshot)
            run.status = "cancelled"
            run.error = {
                "code": "cancelled",
                "message": "取消已传播，研究调用已完成收尾。",
            }
            raise
        except TimeoutError as exc:
            # wait_for 的取消原因保留真实运行快照；超时不丢弃已有 Worker/Review 结果。
            snapshot = getattr(exc.__cause__, "evaluation_snapshot", None) or getattr(
                exc.__cause__, "partial_execution", None
            )
            if snapshot:
                self._attach(run, snapshot)
            if snapshot and (
                (
                    snapshot.get("research", {}).get("status") == "completed"
                    and "report_language" in snapshot
                )
                or (
                    snapshot.get("entry_kind") == "react"
                    and snapshot.get("answer_first", {}).get("answer_status")
                    in {"answered", "partial"}
                )
            ):
                run.status = "completed"
                run.error = {
                    "code": "report_language_timeout",
                    "message": "研究已完成，中文转换超时；原始报告和引用仍可查看。",
                }
            else:
                run.status = "failed"
                run.error = {
                    "code": "research_timeout",
                    "message": "研究超时并已执行收尾；可查看已有事件。",
                }
        except Exception:  # noqa: BLE001 - API 只返回稳定错误，不导出内部异常
            run.status = "failed"
            run.error = {
                "code": "execution_failure",
                "message": "研究运行发生错误；已有事件保留，未伪造报告。",
            }
        finally:
            run.finished_at = time.monotonic()

    def _finished(self, run, task):
        # 排队任务在协程首次执行前就被取消时，_execute 的 finally 还没有机会运行。
        if task.cancelled() and run.status not in TERMINAL:
            run.status = "cancelled"
            run.error = {
                "code": "cancelled",
                "message": "任务已取消，没有启动新的研究调用。",
            }
            run.finished_at = time.monotonic()
        elif not task.cancelled():
            task.exception()
        # 同步 done callback 不能写库；持久化收尾交给受管理的异步任务。
        self._schedule_finalize(run)

    def _is_attached(self, run_id: str) -> bool:
        """本实例是否仍持有该 run 的执行任务或收尾任务。"""

        operation = self._admissions.peek(run_id)
        if operation is not None and not operation.failed:
            # 提交中或已注册都表示归属本实例；失败后不再声称持有。
            return True
        run = self._runs.get(run_id)
        if run is None:
            return False
        if run.task is not None and not run.task.done():
            return True
        return run.finalize_task is not None and not run.finalize_task.done()

    def _persisted_status(self, record, *, replayed: bool = False) -> dict:
        """持久化记录投影；重放不改写数据库、不安排新 Task。"""

        body = {
            "run_id": record.run_id,
            "mode": record.mode,
            "status": record.status,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "question": (record.request_payload or {}).get("question"),
            "paper_ids": list(
                record.effective_paper_ids
                or (record.request_payload or {}).get("paper_ids")
                or []
            ),
            "paper_selection": (record.public_state or {}).get("paper_selection") or {},
            "events_count": record.last_event_seq,
            "cancel_requested": record.cancel_requested,
            "error": record.error,
            "metrics": record.metrics,
            "report_available": record.current_report_revision is not None,
            "historical_status": None,
            "replayed": replayed,
        }
        return body

    def _occupies_capacity(self, run) -> bool:
        """终态但收尾未完成的 run 仍占用 admission 名额。"""

        if run.status not in TERMINAL:
            return True
        return run.finalize_task is not None and not run.finalize_task.done()

    def _schedule_finalize(self, run) -> None:
        if self.lifecycle is None or run.mode != "live":
            return
        if run.finalize_task is not None and not run.finalize_task.done():
            return
        task = asyncio.create_task(self._finalize(run), name=f"finalize:{run.run_id}")
        run.finalize_task = task
        self._finalize_tasks[run.run_id] = task

    async def _finalize(self, run) -> None:
        try:
            await asyncio.wait_for(
                self._finish_persistently(run), self.finalize_timeout
            )
        except TimeoutError:
            run.persistence_error = "finalize_timeout"
            run.persisted = False
        except asyncio.CancelledError:
            # 收尾任务被取消属于明确的失败状态，记录后不再向上抛，
            # 由 close 统一报告未完成情况。
            run.persistence_error = "finalize_cancelled"
        except Exception as exc:  # noqa: BLE001 - 只保留稳定错误码
            run.persistence_error = getattr(exc, "code", type(exc).__name__)
            run.persisted = False

    async def _finish_persistently(self, run) -> None:
        await self._drain_ingress(run)
        if self.lifecycle is None:
            return
        await self.lifecycle.finish(run.run_id, self._snapshot(run))
        run.persisted = True

    async def _drain_ingress(self, run) -> None:
        """停止接收新事件，并等待关闭前已接受的跨线程投递全部执行。"""

        run.ingress_closed = True
        with run.delivery_lock:
            remaining = run.pending_deliveries
        if remaining == 0:
            return
        if run.delivery_event is None:
            run.delivery_event = asyncio.Event()
        with run.delivery_lock:
            if run.pending_deliveries == 0:
                return
        await run.delivery_event.wait()

    @staticmethod
    def _snapshot(run) -> dict:
        return {
            "run_id": run.run_id,
            "status": run.status,
            "error": run.error,
            "events": len(run.events),
            "late_events": run.late_events,
            "report": run.report,
            "finished_at": datetime.now(UTC),
        }

    async def replay(self, case_id):
        if self._closed:
            raise RunManagerError("service_stopping", "服务正在停止。", 503)
        self._prune()
        if len(self._runs) >= self.max_retained_runs:
            raise RunManagerError(
                "history_capacity",
                "任务保留空间已满，运行中的任务不会被回放覆盖。",
                429,
            )
        try:
            execution = self.replays.load(case_id)
        except (KeyError, ReplayError) as exc:
            if isinstance(exc, KeyError) or str(exc) == "unknown_replay_case":
                raise RunManagerError(
                    "replay_not_found", "历史案例不存在。", 404
                ) from None
            raise RunManagerError(
                "replay_unavailable", "历史案例文件缺失或无效，未补写缺失记录。", 503
            ) from None
        except Exception:  # noqa: BLE001 - 损坏历史文件采用失败封闭，不输出原异常
            raise RunManagerError(
                "replay_unavailable", "历史案例文件缺失或无效，未补写缺失记录。", 503
            ) from None
        run = _Run(
            uuid4().hex,
            "replay",
            execution.get("question", ""),
            execution.get("paper_ids", []),
            datetime.now(UTC).isoformat(),
        )
        self._runs[run.run_id] = run
        self._attach(run, execution, source="replay")
        from app.research.report_language import load_replay_translation

        language = load_replay_translation(case_id, run.report, self.replays.root)
        run.report["language"] = sanitize_public(
            language.model_dump(mode="json", exclude={"display_report"})
            | {"source": "offline_replay"}
        )
        run.report["chinese_report"] = (
            sanitize_public(language.display_report)
            if language.status == "translated"
            else None
        )
        if getattr(self.runner, "main_agent_entry", False):
            from app.research.answer_first import historical_answer

            original = run.report
            run.report = historical_answer(original)
            run.report["language"] = original["language"]
            run.report["chinese_report"] = (
                historical_answer(original["chinese_report"])
                if original.get("chinese_report")
                else None
            )
        # 这里只表示历史文件已加载；原研究失败/不足保留在 report、节点和 historical_status。
        run.status = "completed"
        run.finished_at = time.monotonic()
        return self.status(run.run_id)

    def status(self, identity):
        run = self._get(identity)
        research = (run.execution or {}).get("research", run.execution or {})
        return sanitize_public(
            {
                "run_id": run.run_id,
                "mode": run.mode,
                "status": run.status,
                "created_at": run.created_at,
                "question": run.question,
                "paper_ids": run.paper_ids,
                "research_question": run.research_question,
                "query_language": run.query_language,
                "paper_selection": run.paper_selection,
                "answer_status": (run.report or {}).get("answer_status"),
                "execution_mode": (run.report or {}).get("execution_mode"),
                "report_language_status": (run.report or {})
                .get("language", {})
                .get("status"),
                "events_count": len(run.events),
                "event_limit_reached": run.event_limit_reached,
                "cancel_requested": run.cancel_requested,
                "metrics": (run.report or {}).get("metrics", {}),
                "historical_status": research.get("status")
                if run.mode == "replay"
                else None,
                "error": run.error,
                "report_available": run.report is not None
                and run.report.get("status")
                not in {"report_unavailable", "report_invalid"},
                "details_available": run.execution is not None,
            }
        )

    def events(self, identity, after=0):
        run = self._get(identity)
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise RunManagerError("invalid_cursor", "事件游标必须为非负整数。", 422)
        if after > len(run.events):
            raise RunManagerError("invalid_cursor", "事件游标超过当前记录范围。", 422)
        return {
            "events": [
                e.model_dump(mode="json") for e in run.events if e.event_id > after
            ],
            "next_cursor": len(run.events),
        }

    def report(self, identity):
        run = self._get(identity)
        if run.report is None:
            raise RunManagerError(
                "report_not_available", "当前尚未生成可展示报告。", 409
            )
        return run.report

    # ---- 持久化只读查询（Batch C） --------------------------------------
    #
    # 三个 GET 在持久化模式下以数据库为权威：状态、时间、版本、事件与报告
    # 都取自已提交记录，不因内存 TTL、进程重启或内存缺失而丢失，也绝不用
    # 内存状态掩盖数据库读取失败。全部只读：不接管、不重排、不新建 Task。

    async def _persisted_record(self, identity: str):
        """解析一次读取的权威来源，返回 ``(record, memory_run, memory_only)``。

        - 禁用持久化 → 完全沿用内存路径（原有行为不变）；
        - 回放任务不落库，仍由内存回答；
        - 其余一律以数据库记录为准，读不到就明确失败，不做缓存兜底。
        """

        if self.lifecycle is None:
            return None, self._get(identity), True
        memory = self._runs.get(identity)
        if memory is not None and memory.persistence_error:
            raise RunManagerError(
                "run_persistence_incomplete",
                "该任务的持久化写入未可靠完成，请只读核对已有记录。",
                503,
                retry_mode="reconcile_only",
                run_id=identity,
            )
        # 存储层故障由 DatabaseResource 转成 StorageError 上抛 → 503。
        record = await self.lifecycle.get_run(identity)
        if record is not None:
            return record, memory, False
        if memory is not None and memory.mode == "replay":
            return None, memory, True
        if memory is not None:
            # 本实例认为自己在跑，数据库却没有该记录：不猜测，也不拿内存冒充。
            raise RunManagerError(
                "run_persistence_incomplete",
                "该任务在数据库中不可见，未按内存状态回答。",
                503,
                retry_mode="reconcile_only",
                run_id=identity,
            )
        raise RunManagerError("run_not_found", "任务不存在或已过保留期限。", 404)

    @staticmethod
    def _record_status(record, memory) -> dict:
        """数据库投影；内存只补数据库不存的字段（报告派生的展示信息）。"""

        public_state = record.public_state or {}
        payload = record.request_payload or {}
        report = (memory.report if memory is not None else None) or {}
        language = report.get("language")
        language = language if isinstance(language, dict) else {}
        if record.effective_paper_ids:
            paper_ids = list(record.effective_paper_ids)
        else:
            # 实际范围尚未确认：兼容字段只回退到数据库中的原始请求范围。
            paper_ids = list(payload.get("paper_ids") or [])
        metrics = record.metrics or report.get("metrics") or {}
        return sanitize_public(
            {
                "run_id": record.run_id,
                "mode": record.mode,
                "status": record.status,
                "created_at": (
                    record.created_at.isoformat() if record.created_at else None
                ),
                "question": payload.get("question"),
                "paper_ids": paper_ids,
                "research_question": public_state.get("research_question"),
                "query_language": public_state.get("query_language") or {},
                "paper_selection": public_state.get("paper_selection") or {},
                "answer_status": report.get("answer_status"),
                "execution_mode": report.get("execution_mode"),
                "report_language_status": language.get("status"),
                "events_count": record.last_event_seq,
                "event_limit_reached": record.event_limit_reached,
                "cancel_requested": record.cancel_requested,
                "metrics": metrics,
                "historical_status": None,
                "error": record.error,
                "report_available": record.current_report_revision is not None,
                "details_available": record.current_report_revision is not None
                or (memory is not None and memory.execution is not None),
                "started_at": (
                    record.started_at.isoformat() if record.started_at else None
                ),
                "finished_at": (
                    record.finished_at.isoformat() if record.finished_at else None
                ),
                "state_version": record.state_version,
                "persisted": True,
            }
        )

    @staticmethod
    def _persisted_event(run_id: str, row: dict) -> dict:
        occurred_at = row.get("occurred_at")
        try:
            event = RunEvent(
                run_id=run_id,
                event_id=row["event_id"],
                timestamp=occurred_at.isoformat() if occurred_at is not None else None,
                node_type=row["node_type"],
                node_id=row["node_id"],
                status=row["status"],
                event_type=row["event_type"],
                source=row["source"],
                summary=row["summary"],
                details=row.get("details") or {},
            )
        except (ValidationError, KeyError, TypeError):
            raise RunManagerError(
                "run_persistence_incomplete",
                "已提交事件不满足事件契约，未返回不可信数据。",
                503,
                retry_mode="reconcile_only",
                run_id=run_id,
            ) from None
        return event.model_dump(mode="json")

    async def read_status(self, identity):
        record, memory, memory_only = await self._persisted_record(identity)
        if memory_only:
            return self.status(identity)
        return self._record_status(record, memory)

    async def read_events(self, identity, after=0):
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise RunManagerError("invalid_cursor", "事件游标必须为非负整数。", 422)
        record, _memory, memory_only = await self._persisted_record(identity)
        if memory_only:
            return self.events(identity, after)
        if after > record.last_event_seq:
            raise RunManagerError("invalid_cursor", "事件游标超过当前记录范围。", 422)
        rows = await self.lifecycle.get_events(identity, after=after)
        events = [self._persisted_event(identity, row) for row in rows]
        return {
            "events": events,
            # 截断时退回最后一条已提交序号，调用方据此继续翻页。
            "next_cursor": events[-1]["event_id"] if events else after,
            "last_event_seq": record.last_event_seq,
        }

    async def read_report(self, identity):
        """返回 ``(public_payload, meta)``；meta 供响应头携带版本与哈希。"""

        _record, _memory, memory_only = await self._persisted_record(identity)
        if memory_only:
            return self.report(identity), {}
        report = await self.lifecycle.get_current_report(identity)
        if report is None:
            raise RunManagerError(
                "report_not_available", "当前尚未生成可展示报告。", 409
            )
        if report.run_id != identity:
            raise RunManagerError(
                "run_persistence_incomplete",
                "报告归属与请求任务不一致，未返回。",
                503,
                retry_mode="reconcile_only",
                run_id=identity,
            )
        if not report.content_matches():
            raise RunManagerError(
                "run_persistence_incomplete",
                "已提交报告内容与其哈希不一致，未返回。",
                503,
                retry_mode="reconcile_only",
                run_id=identity,
            )
        return sanitize_public(report.public_payload), {
            "revision": report.revision,
            "schema_version": report.schema_version,
            "snapshot_kind": report.snapshot_kind,
            "payload_sha256": report.payload_sha256,
        }

    async def cancel(self, identity):
        run = self._get(identity)
        if (
            run.mode == "live"
            and run.status not in TERMINAL
            and not run.cancel_requested
        ):
            run.cancel_requested = True
            run.task.cancel()
            await asyncio.sleep(0)
        return self.status(identity)

    async def close(self):
        """拒绝新请求 → 取消研究 → 等待投递与收尾 → 关闭 lifecycle。"""

        self._closed = True
        pending = []
        for run in self._runs.values():
            if run.task is not None and not run.task.done():
                if not run.cancel_requested:
                    run.cancel_requested = True
                    run.task.cancel()
                pending.append(run.task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # done callback 已登记收尾任务；这里等待它们，而不是直接关库。
        finalizers = [task for task in self._finalize_tasks.values() if not task.done()]
        if finalizers:
            await asyncio.wait(finalizers, timeout=self.finalize_timeout)
        unfinished = tuple(
            run_id for run_id, task in self._finalize_tasks.items() if not task.done()
        )
        for task in self._finalize_tasks.values():
            if task.done() and not task.cancelled():
                task.exception()

        if self.lifecycle is not None:
            close = getattr(self.lifecycle, "close", None)
            if close is not None:
                await close()
        self._admissions.clear()
        return unfinished
