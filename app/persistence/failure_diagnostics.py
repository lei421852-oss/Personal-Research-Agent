"""受限、非阻塞的首次持久化失败诊断；不包含异常消息或业务数据。"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import re
import threading
from datetime import UTC, datetime

LOGGER = logging.getLogger("app.persistence.first_failure")
_MAX_PENDING = 128
_EVENTS: queue.Queue = queue.Queue(maxsize=_MAX_PENDING)
_START_LOCK = threading.Lock()
_worker = None
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
    elif (
        code in {"storage_pool_timeout", "writer_flush_timeout", "writer_close_timeout"}
        or name == "TimeoutError"
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
        }
        else "unknown",
        "event_type": event_type if event_type in _ALLOWED_EVENT_TYPES else None,
        "write_stage": stage
        if stage in {"queue", "writer_apply", "barrier", "close"}
        else "unknown",
        "exception_type": name,
        "internal_error_code": code,
        "category": category,
        "sqlstate": state,
        "process_id": os.getpid(),
        "first_failure": True,
    }


def _consume():
    while True:
        item = _EVENTS.get()
        try:
            if item is _STOP:
                return
            try:
                LOGGER.error(
                    "%s", json.dumps(item, ensure_ascii=True, separators=(",", ":"))
                )
            except Exception:  # noqa: BLE001, S110 - 不让诊断错误影响业务
                # 诊断失败不能替代原始持久化失败。
                pass
        finally:
            _EVENTS.task_done()


def emit_failure(event):
    """容量有界；后台 logger 不阻塞 Writer。满队列允许丢失并返回 False。"""
    global _worker
    try:
        with _START_LOCK:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(
                    target=_consume, name="persistence-diagnostics", daemon=True
                )
                _worker.start()
        _EVENTS.put_nowait(event)
        return True
    except Exception:  # noqa: BLE001 - 不让诊断错误影响业务
        return False


def flush_diagnostics(timeout=1.0):
    """尽力等待；慢 handler 和强杀时不承诺记录持久化。"""
    worker = _worker
    if worker is None:
        return True
    try:
        _EVENTS.put_nowait(_STOP)
    except queue.Full:
        return False
    worker.join(timeout)
    return not worker.is_alive()


atexit.register(flush_diagnostics)
