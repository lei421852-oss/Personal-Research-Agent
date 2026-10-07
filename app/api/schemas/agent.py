from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictStr,
    model_serializer,
)


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentRunRequest(DTO):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    question: Annotated[StrictStr, Field(min_length=1, max_length=4000, pattern=r"\S")]


class CitationDTO(DTO):
    evidence_id: str
    paper_id: str
    page: int = Field(gt=0)
    figure_id: str | None = None


class TraceStepDTO(DTO):
    step: int = Field(gt=0)
    tool_name: str
    arguments: dict[str, JsonValue]
    status: Literal["success", "empty", "error"]
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence_ids: list[str]
    retrieved_paper_ids: list[str] = Field(default_factory=list)
    failure: dict[str, JsonValue] | None = None

    @model_serializer(mode="wrap")
    def omit_empty_paper_ids(self, handler):
        data = handler(self)
        if not self.retrieved_paper_ids:
            data.pop("retrieved_paper_ids", None)
        return data


class AnalysisDTO(DTO):
    observations: list[str]
    interpretation: str
    evidence_used: list[str]
    limitations: list[str]


class FigureDTO(CitationDTO):
    caption: str
    image_url: str
    analysis: AnalysisDTO | None = None


class MetricsDTO(DTO):
    tool_calls: int = Field(ge=0, le=6)
    model_calls: int = Field(ge=0)
    vlm_calls: int = Field(ge=0)
    total_latency_ms: float = Field(ge=0, allow_inf_nan=False)


class AgentRunResponse(DTO):
    status: Literal[
        "completed",
        "insufficient_evidence",
        "step_exhaustion",
        "repeated_failure",
        "model_failure",
        "protocol_failure",
    ]
    answer: str | None
    citations: list[CitationDTO]
    limitations: list[str]
    trace: list[TraceStepDTO]
    metrics: MetricsDTO
    figures: list[FigureDTO]
    stop_details: dict[str, JsonValue]
    retrieval_mode: Literal["day5-controlled", "real", "test"]


class ErrorDTO(DTO):
    code: str
    message: str
    retryable: bool = False
    fields: list[str] = Field(default_factory=list)


class APIErrorResponse(DTO):
    status: Literal["error"] = "error"
    error: ErrorDTO
