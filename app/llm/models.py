"""LLM 接口使用的统一数据模型。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class ToolCall(BaseModel):
    """模型请求调用一个工具的标准结构。"""

    model_config = ConfigDict(extra="forbid")

    id: StrictStr
    name: StrictStr
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """不依赖具体模型 SDK 的标准响应结构。"""

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
