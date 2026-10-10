"""单进程研究任务管理：后台执行、限流和取消，不参与研究决策。"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from app.research.run_events import RunEvent, sanitize_public
from app.research.run_presenter import execution_events, present_execution
from app.research.run_replay import ReplayError

TERMINAL = frozenset({"completed", "failed", "cancelled"})


class ResearchRunRequest(BaseModel):
    """只接受研究问题、已准备论文身份和有界调用预算。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    question: Annotated[StrictStr, Field(min_length=1, max_length=4000, pattern=r"\S")]
    paper_ids: list[Annotated[StrictStr, Field(min_length=1, max_length=128)]] = Field(
        min_length=2, max_length=3
    )
    max_model_calls: Annotated[StrictInt, Field(ge=1, le=200)] = 200

    @model_validator(mode="after")
    def unique_papers(self):
        if len(self.paper_ids) != len(set(self.paper_ids)):
            raise ValueError("paper_ids must be unique")
        return self


class RunManagerError(Exception):
    def __init__(self, code, message, status_code=409):
        super().__init__(code)
        self.code, self.message, self.status_code = code, message, status_code


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
    ):
        if (
            max_concurrent_runs < 1
            or max_queued_runs < 0
            or max_retained_runs < max_concurrent_runs + max_queued_runs
            or history_ttl_seconds <= 0
            or timeout_seconds <= 0
            or max_events < 1
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

    @staticmethod
    def _fingerprint(request):
        payload = {
            "question": " ".join(request.question.casefold().split()),
            "papers": sorted(request.paper_ids),
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
        run.events.append(
            RunEvent(
                run_id=run.run_id,
                event_id=len(run.events) + 1,
                **event,
            )
        )

    def _sink(self, run):
        loop = asyncio.get_running_loop()

        def emit(**event):
            # Provider/检索由旧适配器在线程中执行；所有事件序号在主循环串行分配。
            def deliver():
                if run.status not in TERMINAL:
                    self._append(run, **event)

            loop.call_soon_threadsafe(deliver)

        return emit

    async def create(self, request: ResearchRunRequest):
        if self._closed:
            raise RunManagerError(
                "service_stopping", "服务正在停止，不能启动新任务。", 503
            )
        self._prune()
        try:
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
            if r.mode == "live" and r.status not in TERMINAL
        ]
        if any(r.fingerprint == fingerprint for r in active):
            raise RunManagerError(
                "duplicate_run", "相同问题与论文的任务仍在执行，请查看已有任务。", 409
            )
        if len(active) >= self.max_concurrent_runs + self.max_queued_runs:
            raise RunManagerError("run_capacity", "研究运行及排队数量已达上限。", 429)
        if not self.dependencies.health()["real_research_ready"]:
            raise RunManagerError(
                "research_unavailable",
                "真实研究依赖尚未就绪；可以查看健康状态或历史回放。",
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
        self._runs[run.run_id] = run
        run.task = asyncio.create_task(
            self._execute(run, request), name=f"research:{run.run_id}"
        )
        run.task.add_done_callback(lambda task: self._finished(run, task))
        return self.status(run.run_id)

    def _attach(self, run, execution, *, source="derived"):
        run.execution = execution
        for event in execution_events(execution, source=source):
            self._append(run, **event)
        run.report = present_execution(execution)

    async def _execute(self, run, request):
        try:
            async with self._slots:
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
                    run.error = {
                        "code": "research_failed",
                        "message": "研究执行失败；已获得的记录和报告仍可查看。",
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
