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


class WorkerStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerError(ResearchModel):
    """公开错误摘要；不保存异常正文、提示词或模型内部推理。"""

    code: NonEmpty
    message: NonEmpty
    requirement_ids: list[NonEmpty] = Field(default_factory=list)
    details: dict[str, JsonValue] | None = None


class WorkerTrace(ResearchModel):
    worker_task_id: NonEmpty
    paper_id: NonEmpty
    requirement_count: int = Field(ge=1)
    resolved_count: int = Field(ge=0)
    status: WorkerStatus
    tool_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    vlm_tool_attempts: int = Field(default=0, ge=0)
    # 已提供的策略纠正机会，独立于 Provider 的物理重试次数。
    protocol_corrections: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    # 分母统计逻辑依赖调用；物理重试次数单独计数。
    dependency_calls: int = Field(default=0, ge=0)
    retried_dependency_calls: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    # 只保存工具/需求更新的公开动作；调用方须脱敏，不保存模型推理。
    actions: list[dict[str, JsonValue]] = Field(default_factory=list)

    @model_validator(mode="after")
    def count_invariants(self):
        if self.resolved_count > self.requirement_count:
            raise ValueError("Resolved count exceeds requirement count")
        if self.retried_dependency_calls > self.dependency_calls:
            raise ValueError("Retried calls exceed logical dependency calls")
        if self.retried_dependency_calls > self.retry_count:
            raise ValueError("Retried calls exceed physical retry attempts")
        return self


class WorkerResult(ResearchModel):
    worker_task_id: NonEmpty
    paper_id: NonEmpty
    status: WorkerStatus
    requirements: list[EvidenceRequirement] = Field(min_length=1)
    error: WorkerError | None = None
    attempts: int = Field(default=1, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    trace: WorkerTrace | None = None
    # 来自运行时核验后的观察账本；候选关联和最终引用不能代替此集合。
    observed_evidence_ids: list[NonEmpty] = Field(default_factory=list)

    @model_validator(mode="after")
    def execution_invariants(self):
        if len(set(self.observed_evidence_ids)) != len(self.observed_evidence_ids):
            raise ValueError("Duplicate observed evidence ID")
        ids = [item.requirement_id for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate worker result requirement")
        if any(item.paper_id != self.paper_id for item in self.requirements):
            raise ValueError("Worker result requirements must belong to one paper")
        if any(item.status == RequirementStatus.PENDING for item in self.requirements):
            raise ValueError("Worker result cannot retain pending requirements")
        resolved = sum(
            item.status
            in (RequirementStatus.SUPPORTED, RequirementStatus.INSUFFICIENT_EVIDENCE)
            for item in self.requirements
        )
        if self.status == WorkerStatus.COMPLETED and resolved != len(self.requirements):
            raise ValueError("Completed worker needs every requirement resolved")
        if self.status == WorkerStatus.PARTIAL_SUCCESS and not (
            0 < resolved < len(self.requirements)
        ):
            raise ValueError("Partial success needs resolved and failed requirements")
        if self.status == WorkerStatus.FAILED and resolved:
            raise ValueError("Failed worker cannot contain resolved requirements")
        if self.status == WorkerStatus.CANCELLED and not any(
            item.status == RequirementStatus.CANCELLED for item in self.requirements
        ):
            raise ValueError("Cancelled worker needs a cancelled requirement")
        if self.trace is not None and (
            self.trace.worker_task_id != self.worker_task_id
            or self.trace.paper_id != self.paper_id
            or self.trace.status != self.status
            or self.trace.requirement_count != len(self.requirements)
            or self.trace.resolved_count != resolved
        ):
            raise ValueError("Worker trace does not match result")
        return self


class OrchestratorTrace(ResearchModel):
    """池级公开计数；不包含 Worker 提示词或研究过程的隐藏推理。"""

    worker_task_count: int = Field(ge=0)
    completed_count: int = Field(default=0, ge=0)
    partial_success_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    cancelled_count: int = Field(default=0, ge=0)
    peak_worker_concurrency: int = Field(default=0, ge=0)
    peak_llm_concurrency: int = Field(default=0, ge=0)
    peak_vlm_concurrency: int = Field(default=0, ge=0)
    peak_retrieval_concurrency: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0, ge=0)
    resolution_rate: float = Field(default=0, ge=0, le=1)
    coverage: float = Field(default=0, ge=0, le=1)
    worker_success_rate: float = Field(default=0, ge=0, le=1)
    partial_failure_rate: float = Field(default=0, ge=0, le=1)
    retry_rate: float = Field(default=0, ge=0, le=1)
    dependency_calls: int = Field(default=0, ge=0)
    retried_dependency_calls: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
