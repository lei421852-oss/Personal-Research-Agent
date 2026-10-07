"""公开工具观察结果与执行事实，不包含模型隐藏推理。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.evidence.models import NonEmpty

Status = Literal["success", "empty", "error"]
IssueCode = Literal[
    "redundant_action",
    "invalid_arguments",
    "unknown_paper",
    "empty_result",
    "dependency_failure",
    "internal_failure",
    "unknown_tool",
    "invalid_evidence",
    "citation_validation_failure",
]


class ObservationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: IssueCode
    retryable: bool


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Status
    tool_name: NonEmpty
    summary: NonEmpty
    payload: dict[str, JsonValue]
    issue: ObservationIssue | None = None

    @property
    def result_count(self) -> int:
        """兼容文本列表、图像列表和单张 Figure 三种 payload，统计返回证据数。"""
        count = len(self.payload.get("evidence", [])) + len(
            self.payload.get("figures", [])
        )
        count += len(self.payload.get("papers", []))
        return count + int(self.payload.get("figure") is not None)

    @model_validator(mode="after")
    def check_status(self):
        """约束状态与证据、错误码一致，防止将空结果误报为工具故障。"""
        for key in ["evidence", "figures", "papers"]:
            if key in self.payload and not isinstance(self.payload[key], list):
                raise ValueError("Evidence collections must be lists")
        if self.payload.get("figure") is not None and not isinstance(
            self.payload["figure"], dict
        ):
            raise ValueError("Figure result must be an object")
        if self.status == "success" and self.issue is not None:
            raise ValueError("Successful observation cannot carry an issue")
        if self.status != "success" and self.issue is None:
            raise ValueError("Empty and error observations require an issue")
        if self.status == "empty" and self.issue.code != "empty_result":
            raise ValueError("Empty result is not a tool failure")
        if self.status == "error" and self.issue.code == "empty_result":
            raise ValueError("Empty result cannot be reported as failure")
        if self.status != "success" and self.result_count:
            raise ValueError("Failed/empty observations cannot contain evidence")
        if (
            self.tool_name
            in {"search_text", "find_figure", "read_page", "search_papers"}
            and self.status == "success"
            and not self.result_count
        ):
            raise ValueError("Paper tool success requires evidence")
        if self.issue is not None and self.issue.retryable != (
            self.issue.code == "dependency_failure"
        ):
            raise ValueError("Only dependency failures are retryable in this phase")
        return self


class ToolExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_call_id: NonEmpty
    tool_name: NonEmpty
    arguments: dict[str, JsonValue]
    status: Status
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    result_count: int = Field(ge=0)
    failure: dict[str, JsonValue] | None = None
