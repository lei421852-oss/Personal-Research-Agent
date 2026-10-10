"""工作台公开事件契约；只描述执行事实，不保存模型内部推理。"""

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.tools.redaction import public_value, redact_text

NodeType = Literal[
    "Planner",
    "Worker",
    "Validator",
    "Reviewer",
    "Recovery",
    "Synthesis",
    "QueryLanguage",
    "PaperSelection",
    "ReportLanguage",
    "MainAgent",
    "PaperSearch",
    "QueryExpansion",
    "Evidence",
    "ResearchTool",
]
NodeStatus = Literal["pending", "running", "completed", "failed", "skipped"]
EventType = Literal["started", "progress", "completed", "failed", "skipped"]
EventSource = Literal["live", "derived", "replay"]

# 原始模型请求、响应及推理不是公开诊断字段；敏感值也不保留占位副本。
_PRIVATE_KEY = re.compile(
    r"reasoning|chain.?of.?thought|internal.?thought|hidden|prompt|messages?"
    r"|raw|provider.?response|provider.?request|api.?key|secret|password"
    r"|credential|authorization|(?:^|_)token(?:$|_)|filesystem|asset.?path|traceback",
    re.IGNORECASE,
)


def safe_details(value: Any) -> JsonValue:
    """从公开 DTO 递归去掉私有字段；不调用任意对象的 repr。"""
    if isinstance(value, Mapping):
        cleaned = {
            str(key): safe_details(item)
            for key, item in value.items()
            if isinstance(key, str) and not _PRIVATE_KEY.search(key)
        }
        return public_value(cleaned)
    if isinstance(value, (list, tuple)):
        return [safe_details(item) for item in value]
    return public_value(value)


class RunEvent(BaseModel):
    """事件序号由运行管理器分配；回放缺少时间时明确保留 null。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    event_id: int = Field(ge=1, strict=True)
    timestamp: str | None = None
    node_type: NodeType
    node_id: str = Field(min_length=1)
    status: NodeStatus
    event_type: EventType
    summary: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    source: EventSource

    @field_validator("run_id", "timestamp", "node_id", "summary")
    @classmethod
    def public_text(cls, value):
        return redact_text(value) if isinstance(value, str) else value

    @field_validator("details", mode="before")
    @classmethod
    def public_details(cls, value):
        return safe_details(value)


# 命名别名方便调用方读出用途，序列化仍只有同一份事件契约。
ResearchRunEvent = RunEvent
sanitize_public = safe_details
