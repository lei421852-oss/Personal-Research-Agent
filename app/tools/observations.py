"""Public tool observations and execution facts, never model reasoning."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.evidence.models import NonEmpty

Status = Literal["success", "empty", "error"]
IssueCode = Literal[
    "invalid_arguments",
    "unknown_paper",
    "empty_result",
    "dependency_failure",
    "internal_failure",
    "unknown_tool",
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
        count = len(self.payload.get("evidence", [])) + len(
            self.payload.get("figures", [])
        )
        return count + int(self.payload.get("figure") is not None)

    @model_validator(mode="after")
    def check_status(self):
        for key in ["evidence", "figures"]:
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
            self.tool_name in {"search_text", "find_figure", "read_page"}
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
