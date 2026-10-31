"""受限、非阻塞的首次持久化失败诊断；不包含异常消息或业务数据。"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import UTC, datetime

LOGGER = logging.getLogger("app.persistence.first_failure")
_MAX_PENDING = 128
_EVENTS: queue.Queue = queue.Queue(maxsize=_MAX_PENDING)
_START_LOCK = threading.Lock()
_worker = None
_state = "STOPPED"
_started_once = False
_stop_consumed = False
_STATS = {
    "queue_dropped_total": 0,
    "emit_errors_total": 0,
    "handler_errors_total": 0,
    "worker_restarts_total": 0,
}
_STOP = object()
_ALLOWED_CODES = frozenset(
    {
        "event_cursor_conflict",
        "event_sequence_gap",
        "storage_unavailable",
        "storage_pool_timeout",
        "storage_cleanup_failed",
        "writer_failed",
        "writer_queue_full",
        "writer_flush_timeout",
        "writer_close_timeout",
        "run_not_found",
        "finalize_cancelled",
        "finalize_outcome_unknown",
        "finalize_content_conflict",
        "finalize_pending_review",
        "finalize_reconcile_unavailable",
    }
)
_ALLOWED_TYPES = frozenset(
    {
        "StorageError",
        "ConflictError",
        "RepositoryError",
        "WriterFailed",
        "WriterFull",
        "TimeoutError",
        "OSError",
        "IntegrityError",
        "OperationalError",
        "DBAPIError",
        "PoolTimeout",
        "CancelledError",
    }
)
_ALLOWED_EVENT_TYPES = frozenset(
    {"started", "progress", "completed", "failed", "skipped"}
)


def safe_sqlstate(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value)
        else None
    )


def safe_database_metadata(exc):
    """仅提取 SQLSTATE 与已知异常类型，不保留异常对象或字符串。"""
    try:
        original = getattr(exc, "orig", None)
        for item in (exc, original, getattr(original, "__cause__", None)):
            if item is None:
                continue
            state = safe_sqlstate(getattr(item, "sqlstate", None)) or safe_sqlstate(
                getattr(item, "pgcode", None)
            )
            if state:
                return state
    except Exception:  # noqa: BLE001 - 安全诊断不能覆盖数据库异常
        return None
    return None


def build_failure(*, run_id, operation, stage, event_type, exc, reason):
    identity = (
        run_id
        if isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id)
        else None
    )
    name = type(exc).__name__ if exc is not None else "UnknownError"
    name = name if name in _ALLOWED_TYPES else "OtherError"
    code = getattr(exc, "code", None) if exc is not None else None
    if code not in _ALLOWED_CODES:
        code = reason if reason in _ALLOWED_CODES else None
    state = safe_sqlstate(getattr(exc, "sqlstate", None)) if exc is not None else None
    if code == "event_cursor_conflict":
        category = "event_cursor_conflict"
    elif code in {"writer_flush_timeout", "writer_close_timeout"} or (
        code == "writer_failed" and getattr(exc, "detail", None) == "write_timeout"
    ):
        category = "writer_timeout"
    elif (
        code == "storage_pool_timeout"
        or name == "PoolTimeout"
        or (name == "TimeoutError" and type(exc).__module__ == "sqlalchemy.exc")
    ):
        category = "database_timeout"
    elif state and state.startswith("23"):
        category = "database_constraint_error"
    elif state and state.startswith("08"):
        category = "database_connection_error"
    elif state and state.startswith("40"):
        category = "transaction_error"
    elif code in {"writer_failed", "writer_queue_full"}:
        category = "writer_internal_error"
    else:
        category = "unknown_persistence_error"
    return {
        "event": "persistence.first_failure",
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": identity,
        "correlation_id": None,
        "operation": operation
        if operation
        in {
            "append_events",
            "save_report",
            "save_paper_selection",
            "queue_submit",
            "barrier",
            "close",
            "finalize",
            "reconcile",
        }
        else "unknown",
        "event_type": event_type if event_type in _ALLOWED_EVENT_TYPES else None,
        "write_stage": stage
        if stage
        in {
            "queue",
            "writer_apply",
            "barrier",
            "close",
            "coordinator_finalize",
            "coordinator_reconcile",
        }
        else "unknown",
        "exception_type": name,
        "internal_error_code": code,
        "category": category,
        "sqlstate": state,
        "process_id": os.getpid(),
        "first_failure": True,
    }


def get_diagnostics_stats():
    """返回独立计数器副本；不依赖可能损坏的日志处理器。"""
    with _START_LOCK:
        return dict(_STATS)


def record_emit_error():
    """统计诊断构造或投递之外层可检测到的失败，不递归调用日志。"""
    try:
        with _START_LOCK:
            _STATS["emit_errors_total"] += 1
    except Exception:  # noqa: BLE001 - 计数失败不能影响持久化
        return


def _consume():
    global _stop_consumed
    while True:
        item = _EVENTS.get()
        try:
            if item is _STOP:
                with _START_LOCK:
                    _stop_consumed = True
                    # 与具体 Worker 绑定，避免旧 flush 误等待新一代线程。
                    threading.current_thread()._persistence_stop_consumed = True
                return
            try:
                LOGGER.error(
                    "%s", json.dumps(item, ensure_ascii=True, separators=(",", ":"))
                )
            except BaseException:  # noqa: BLE001 - 后台日志故障不能影响业务
                with _START_LOCK:
                    _STATS["handler_errors_total"] += 1
        finally:
            _EVENTS.task_done()


def emit_failure(event):
    """状态与投递共用短锁；不会等待后台 handler。"""
    global _worker, _state, _started_once
    try:
        with _START_LOCK:
            if _state == "STOPPING":
                if (_worker is not None and _worker.is_alive()) or not _stop_consumed:
                    _STATS["emit_errors_total"] += 1
                    return False
                _state = "STOPPED"
                _worker = None
            if _state == "RUNNING" and (_worker is None or not _worker.is_alive()):
                _state = "STOPPED"
                _worker = None
            if _state == "STOPPED":
                worker = threading.Thread(
                    target=_consume, name="persistence-diagnostics", daemon=True
                )
                worker.start()
                _worker = worker
                _state = "RUNNING"
                if _started_once:
                    _STATS["worker_restarts_total"] += 1
                _started_once = True
            try:
                _EVENTS.put_nowait(event)
            except queue.Full:
                _STATS["queue_dropped_total"] += 1
                return False
            return True
    except Exception:  # noqa: BLE001 - 不让诊断错误影响业务
        with _START_LOCK:
            _STATS["emit_errors_total"] += 1
        return False


def flush_diagnostics(timeout=1.0):
    """同一生命周期只提交一个 STOP；线程异常退出时限时恢复排空。"""
    global _worker, _state, _stop_consumed
    deadline = time.monotonic() + max(0.0, timeout)
    with _START_LOCK:
        if _state == "STOPPED":
            return True
        worker = _worker
        if _state == "RUNNING" and (worker is None or not worker.is_alive()):
            if _EVENTS.empty():
                _worker = None
                _state = "STOPPED"
                return True
            try:
                worker = threading.Thread(
                    target=_consume, name="persistence-diagnostics", daemon=True
                )
                worker.start()
            except Exception:  # noqa: BLE001 - 启动失败不能误报 flush 成功
                _STATS["emit_errors_total"] += 1
                return False
            _worker = worker
            _STATS["worker_restarts_total"] += 1
        if _state == "RUNNING":
            try:
                _stop_consumed = False
                _EVENTS.put_nowait(_STOP)
            except queue.Full:
                return False
            _state = "STOPPING"
    while worker is not None:
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            return False
        with _START_LOCK:
            if _worker is not worker:
                if getattr(worker, "_persistence_stop_consumed", False):
                    return True
                if _state == "STOPPED":
                    return True
                worker = _worker
                continue
            if _stop_consumed:
                _worker = None
                _state = "STOPPED"
                return True
            # STOP 尚未消费，原队列已接受事件必须交由新 Worker 处理。
            if time.monotonic() >= deadline:
                return False
            try:
                worker = threading.Thread(
                    target=_consume, name="persistence-diagnostics", daemon=True
                )
                worker.start()
            except Exception:  # noqa: BLE001 - 记录启动失败，不丢弃已接受事件
                _STATS["emit_errors_total"] += 1
                return False
            _worker = worker
            _STATS["worker_restarts_total"] += 1
    return False


atexit.register(flush_diagnostics)
