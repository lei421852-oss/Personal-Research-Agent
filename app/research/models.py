"""研究计划、证据需求及完成语义；不依赖检索器、API 或模型提供方。"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    field_validator,
    model_validator,
)

from app.evidence.models import NonEmpty

MAX_DIMENSIONS = 8
DimensionID = Annotated[NonEmpty, Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ResearchDimension(ResearchModel):
    dimension_id: DimensionID
    name: Annotated[NonEmpty, Field(max_length=120)]
    description: Annotated[NonEmpty, Field(max_length=1000)]
    research_question: Annotated[NonEmpty, Field(max_length=1500)]


class DimensionProposal(ResearchModel):
    """LLM 只提交维度；不允许夹带逐篇答案或 EvidenceRequirements。"""

    dimensions: list[ResearchDimension] = Field(min_length=1, max_length=MAX_DIMENSIONS)

    @field_validator("dimensions")
    @classmethod
    def unique_dimensions(cls, dimensions):
        identities = [d.dimension_id for d in dimensions]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate dimension_id")
        questions = [
            " ".join(d.research_question.casefold().split()) for d in dimensions
        ]
        if len(set(questions)) != len(questions):
            raise ValueError("Duplicate research_question")
        return dimensions


class ResearchPlan(DimensionProposal):
    question: NonEmpty
    paper_ids: list[NonEmpty] = Field(min_length=1)

    @field_validator("paper_ids")
    @classmethod
    def unique_papers(cls, identities):
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate paper_id")
        return identities


class CompactPaperMetadata(ResearchModel):
    """可选的紧凑目标信息，不提供画像、全文或预测答案。"""

    paper_id: NonEmpty
    title: NonEmpty


class RequirementStatus(StrEnum):
    PENDING = "pending"
    SUPPORTED = "supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EXECUTION_FAILED = "execution_failed"
    CANCELLED = "cancelled"


class EvidenceRequirement(ResearchModel):
    requirement_id: NonEmpty
    paper_id: NonEmpty
    dimension_id: DimensionID
    question: NonEmpty
    required: StrictBool = True
    status: RequirementStatus = RequirementStatus.PENDING
    claim: NonEmpty | None = None
    evidence_ids: list[NonEmpty] = Field(default_factory=list)

    @model_validator(mode="after")
    def status_invariant(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate evidence_id")
        if self.status == RequirementStatus.SUPPORTED:
            if self.claim is None or not self.evidence_ids:
                raise ValueError("Supported requirement needs a claim and evidence IDs")
        elif self.claim is not None or self.evidence_ids:
            raise ValueError(
                "Unresolved or insufficient requirement cannot contain a claim or evidence"
            )
        return self


class WorkerTask(ResearchModel):
    worker_task_id: NonEmpty
    paper_id: NonEmpty
    requirements: list[EvidenceRequirement] = Field(min_length=1)

    @model_validator(mode="after")
    def paper_scope(self):
        if any(r.paper_id != self.paper_id for r in self.requirements):
            raise ValueError("Worker requirements must belong to one paper")
        ids = [r.requirement_id for r in self.requirements]
        dimensions = [r.dimension_id for r in self.requirements]
        if len(set(ids)) != len(ids) or len(set(dimensions)) != len(dimensions):
            raise ValueError("Duplicate worker requirement")
        return self


class ResearchCompletion(ResearchModel):
    total_requirements: int = Field(ge=0)
    resolved_requirements: int = Field(ge=0)
    supported_requirements: int = Field(ge=0)
    resolution_rate: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    research_complete: bool


class PlannerTrace(ResearchModel):
    """只记录公开执行事实，不保存模型请求、系统提示或隐藏推理。"""

    events: list[str] = Field(default_factory=list)
    status: Literal["success", "error"]
    candidate_paper_count: int = Field(default=0, ge=0)
    dimension_count: int = Field(default=0, ge=0)
    dimension_ids: list[str] = Field(default_factory=list)
    requirement_count: int = Field(default=0, ge=0)
    worker_task_count: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    latency_ms: float = Field(ge=0)
    error_code: str | None = None
    error_message: str | None = None
    failure: dict[str, JsonValue] | None = None


class ResearchPreparation(ResearchModel):
    plan: ResearchPlan
    requirements: list[EvidenceRequirement]
    worker_tasks: list[WorkerTask]
    trace: PlannerTrace
