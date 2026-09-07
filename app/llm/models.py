"""Provider-neutral data models used at the LLM boundary."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class ToolCall(BaseModel):
    """A normalized request from an LLM to invoke one tool."""

    model_config = ConfigDict(extra="forbid")

    id: StrictStr
    name: StrictStr
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """A normalized LLM response independent of any provider SDK."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
