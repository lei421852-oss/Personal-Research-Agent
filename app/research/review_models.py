"""确定性校验和语义审核的独立模型；不改写 Worker 的原始研究结果。"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictInt, model_validator

from app.evidence.models import NonEmpty, Page
from app.research.models import EvidenceRequirement, RequirementStatus, ResearchModel

ReviewReason = Literal[
    "evidence_supports_claim",
    "evidence_partially_supports_claim",
    "evidence_does_not_support_claim",
    "evidence_missing",
    "invalid_structure",
]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]


class ReviewStatus(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
    NOT_REVIEWABLE = "not_reviewable"


class ReviewValidationStatus(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ReviewEvidence(ResearchModel):
    """仅携带真实证据内容和来源，不包含本地文件路径或图片加载能力。"""

    evidence_id: NonEmpty
    paper_id: NonEmpty
    page: Page
    modality: Literal["text", "figure"]
    text: NonEmpty
    caption: NonEmpty | None = None
    figure_id: NonEmpty | None = None


class ReviewTask(ResearchModel):
    worker_task_id: NonEmpty
    requirement: EvidenceRequirement
    evidence: list[ReviewEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def supported_scope(self):
        if self.requirement.status != RequirementStatus.SUPPORTED:
            raise ValueError("Only supported requirements enter semantic review")
        identities = [item.evidence_id for item in self.evidence]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate review evidence ID")
        if set(identities) != set(self.requirement.evidence_ids):
            raise ValueError("Review evidence must match cited evidence exactly")
        if any(item.paper_id != self.requirement.paper_id for item in self.evidence):
            raise ValueError("Review evidence must belong to the requirement paper")
        return self


class ReviewIssue(ResearchModel):
    code: NonEmpty
    message: NonEmpty
    details: dict[str, JsonValue] | None = None


class ReviewTrace(ResearchModel):
    model_calls: NonnegativeInt = 0
    retry_count: NonnegativeInt = 0
    latency_ms: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def physical_counts(self):
        if self.retry_count > self.model_calls:
            raise ValueError("Retry count exceeds model calls")
        return self


class ReviewResult(ResearchModel):
    worker_task_id: NonEmpty
    requirement_id: NonEmpty
    paper_id: NonEmpty
    status: ReviewStatus
    reason_code: ReviewReason | None = None
    claim: NonEmpty | None = None
    evidence_ids: list[NonEmpty] = Field(default_factory=list)
    validation_status: ReviewValidationStatus
    skip_reason: NonEmpty | None = None
    issue: ReviewIssue | None = None
    trace: ReviewTrace = Field(default_factory=ReviewTrace)

    @model_validator(mode="after")
    def review_semantics(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate reviewed evidence ID")
        if self.status in {ReviewStatus.PASSED, ReviewStatus.REJECTED}:
            if (
                self.validation_status != ReviewValidationStatus.PASSED
                or self.claim is None
                or not self.evidence_ids
                or self.issue is not None
                or self.skip_reason is not None
            ):
                raise ValueError("Semantic review requires a valid cited claim")
            expected = (
                {"evidence_supports_claim"}
                if self.status == ReviewStatus.PASSED
                else {
                    "evidence_partially_supports_claim",
                    "evidence_does_not_support_claim",
                }
            )
            if self.reason_code not in expected:
                raise ValueError("Review status and semantic reason do not match")
        elif self.reason_code in {
            "evidence_supports_claim",
            "evidence_partially_supports_claim",
            "evidence_does_not_support_claim",
        }:
            raise ValueError(
                "An execution or validation failure is not a semantic verdict"
            )
        if self.validation_status == ReviewValidationStatus.REJECTED and (
            self.status != ReviewStatus.NOT_REVIEWABLE
            or self.reason_code not in {"invalid_structure", "evidence_missing"}
            or self.issue is None
        ):
            raise ValueError("Deterministic rejection needs a typed validation issue")
        if self.validation_status == ReviewValidationStatus.SKIPPED and (
            self.status != ReviewStatus.NOT_REVIEWABLE
            or self.skip_reason is None
            or self.reason_code is not None
        ):
            raise ValueError("Skipped requirements do not have a semantic verdict")
        return self


class ReviewProposal(ResearchModel):
    """模型只能选择语义结论，身份、Claim 和引用由程序从输入任务绑定。"""

    status: Literal["passed", "rejected"]
    reason_code: Literal[
        "evidence_supports_claim",
        "evidence_partially_supports_claim",
        "evidence_does_not_support_claim",
    ]

    @model_validator(mode="after")
    def verdict_matches(self):
        if (self.status == "passed") != (self.reason_code == "evidence_supports_claim"):
            raise ValueError("Semantic verdict and reason do not match")
        return self


class ReviewPoolTrace(ResearchModel):
    total_requirement_count: NonnegativeInt = 0
    reviewable_requirement_count: NonnegativeInt = 0
    reviewed_count: NonnegativeInt = 0
    passed_count: NonnegativeInt = 0
    rejected_count: NonnegativeInt = 0
    not_reviewable_count: NonnegativeInt = 0
    bypassed_count: NonnegativeInt = 0
    reviewer_model_calls: NonnegativeInt = 0
    retry_count: NonnegativeInt = 0
    peak_reviewer_concurrency: NonnegativeInt = 0
    peak_llm_concurrency: NonnegativeInt = 0
    total_latency_ms: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def count_consistency(self):
        if self.reviewed_count != self.passed_count + self.rejected_count:
            raise ValueError("Reviewed count must equal passed plus rejected")
        if (
            self.total_requirement_count
            != self.reviewed_count + self.not_reviewable_count
        ):
            raise ValueError("Total count must include every review outcome")
        if self.reviewed_count > self.reviewable_requirement_count:
            raise ValueError("Reviewed count exceeds reviewable requirements")
        if self.reviewable_requirement_count > self.total_requirement_count:
            raise ValueError("Reviewable count exceeds total requirements")
        if self.bypassed_count > self.not_reviewable_count:
            raise ValueError("Bypassed count exceeds not-reviewable outcomes")
        if (
            self.reviewable_requirement_count + self.bypassed_count
            != self.total_requirement_count
        ):
            raise ValueError(
                "Reviewable and bypassed counts must cover every requirement"
            )
        if self.retry_count > self.reviewer_model_calls:
            raise ValueError("Review retry count exceeds model calls")
        return self
