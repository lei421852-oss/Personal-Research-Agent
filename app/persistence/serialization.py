"""终态命令与规范哈希；不依赖 SQLAlchemy，可完全离线测试。

哈希口径与 H2.2 阶段 2.0 定稿一致：
- report_content 覆盖 schema_version / snapshot_kind / public_payload /
  authority_payload / source_reference 五个持久化字段；
- terminal 覆盖运行终态与报告身份（revision / revision_token / payload_sha256），
  因此同一 finalize_token 不可能对应两份不同内容。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
SNAPSHOT_KINDS = frozenset({"answer_first", "execution_report"})
HEX64_CHARS = frozenset("0123456789abcdef")
HEX32_CHARS = HEX64_CHARS


def _reject_non_json(value: Any):
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """稳定序列化：键排序、无多余空白、保留非 ASCII 原文。"""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_reject_non_json,
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_isoformat(moment: datetime) -> str:
    """固定为微秒精度、UTC 时区偏移，保证跨重试取值一致。"""

    if moment.tzinfo is None:
        raise ValueError("finished_at must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _is_hex(value: str | None, length: int) -> bool:
    # 不能用 str.islower()：纯数字字符串没有大小写字符，islower() 会返回 False。
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in HEX64_CHARS for character in value)
    )


@dataclass(frozen=True, slots=True)
class ReportCommand:
    """一份不可变报告写入命令；revision 在事务外预分配。"""

    revision: int
    revision_token: str
    expected_current_revision: int | None
    schema_version: int
    snapshot_kind: str
    public_payload: dict
    payload_sha256: str
    authority_payload: dict | None = None
    source_reference: dict | None = None

    def content(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "snapshot_kind": self.snapshot_kind,
            "public_payload": self.public_payload,
            "authority_payload": self.authority_payload,
            "source_reference": self.source_reference,
        }

    def identity(self) -> dict:
        return {
            "revision": self.revision,
            "revision_token": self.revision_token,
            "payload_sha256": self.payload_sha256,
        }


def build_report_command(
    *,
    expected_current_revision: int | None,
    revision_token: str,
    schema_version: int,
    snapshot_kind: str,
    public_payload: dict,
    authority_payload: dict | None = None,
    source_reference: dict | None = None,
) -> ReportCommand:
    if schema_version < 1:
        raise ValueError("schema_version must be >= 1")
    if snapshot_kind not in SNAPSHOT_KINDS:
        raise ValueError("unknown snapshot_kind")
    if not _is_hex(revision_token, 32):
        raise ValueError("revision_token must be 32 lowercase hex characters")
    if expected_current_revision is not None and expected_current_revision < 0:
        raise ValueError("expected_current_revision must be >= 0")
    revision = (expected_current_revision or 0) + 1
    content = {
        "schema_version": schema_version,
        "snapshot_kind": snapshot_kind,
        "public_payload": public_payload,
        "authority_payload": authority_payload,
        "source_reference": source_reference,
    }
    return ReportCommand(
        revision=revision,
        revision_token=revision_token,
        expected_current_revision=expected_current_revision,
        schema_version=schema_version,
        snapshot_kind=snapshot_kind,
        public_payload=public_payload,
        payload_sha256=sha256_hex(content),
        authority_payload=authority_payload,
        source_reference=source_reference,
    )


@dataclass(frozen=True, slots=True)
class FinalizeCommand:
    """一次终态操作；哈希在首次尝试前固化，重试只能复用同一实例。"""

    run_id: str
    finalize_token: str
    expected_state_version: int
    status: str
    finished_at: str
    error: dict | None
    metrics: dict
    expected_last_event_seq: int
    final_last_event_seq: int
    expected_current_revision: int | None
    finalize_sha256: str
    report: ReportCommand | None = None

    def content(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "finished_at": self.finished_at,
            "error": self.error,
            "metrics": self.metrics,
            "events": {
                "expected": self.expected_last_event_seq,
                "final": self.final_last_event_seq,
            },
            "reports": {
                "expected_current_revision": self.expected_current_revision,
                "report": None if self.report is None else self.report.identity(),
            },
        }


def build_finalize_command(
    *,
    run_id: str,
    finalize_token: str,
    expected_state_version: int,
    status: str,
    finished_at: datetime,
    error: dict | None,
    metrics: dict,
    expected_last_event_seq: int,
    final_last_event_seq: int,
    expected_current_revision: int | None,
    report: ReportCommand | None = None,
) -> FinalizeCommand:
    if not _is_hex(run_id, 32):
        raise ValueError("run_id must be 32 lowercase hex characters")
    if not _is_hex(finalize_token, 32):
        raise ValueError("finalize_token must be 32 lowercase hex characters")
    if status not in TERMINAL_STATUSES:
        raise ValueError("finalize status must be terminal")
    if expected_state_version < 0:
        raise ValueError("expected_state_version must be >= 0")
    if final_last_event_seq < expected_last_event_seq:
        raise ValueError("final_last_event_seq cannot move backwards")
    if (
        report is not None
        and report.expected_current_revision != expected_current_revision
    ):
        raise ValueError("report predecessor must match expected_current_revision")
    command = FinalizeCommand(
        run_id=run_id,
        finalize_token=finalize_token,
        expected_state_version=expected_state_version,
        status=status,
        finished_at=utc_isoformat(finished_at),
        error=error,
        metrics=metrics,
        expected_last_event_seq=expected_last_event_seq,
        final_last_event_seq=final_last_event_seq,
        expected_current_revision=expected_current_revision,
        finalize_sha256="",
        report=report,
    )
    return replace(command, finalize_sha256=sha256_hex(command.content()))
