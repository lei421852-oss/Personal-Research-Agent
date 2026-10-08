"""一次定向恢复的身份、预算和历史契约，不提供通用工作流或 Resume。"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.evidence.models import NonEmpty
from app.research.models import (
    EvidenceRequirement,
    ResearchModel,
    WorkerResult,
    WorkerTask,
)
from app.research.requirements import stable_id
from app.research.review_models import NonnegativeInt, ReviewResult
from app.research.runtime import PositiveInt
from app.research.synthesis_models import ResearchReport, SynthesisInput


def claim_ref(requirement):
    return stable_id(
        "claim_",
        requirement.requirement_id,
        requirement.claim,
        *requirement.evidence_ids,
    )


def result_ref(kind, result):
    serialized = json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return kind + "_" + hashlib.sha256(serialized.encode()).hexdigest()[:32]


class RecoveryInputError(ValueError):
    """固定公开代码，不复制不可信 Claim 或异常正文。"""


class RecoveryBudget(ResearchModel):
    model_calls: NonnegativeInt
    tool_calls: NonnegativeInt


class RecoveryConfig(ResearchModel):
    max_recovery_rounds: Annotated[PositiveInt, Field(le=1)] = 1
    max_model_calls_per_worker: PositiveInt = 12
    max_tool_calls_per_worker: NonnegativeInt = 4
    max_model_calls_per_review: PositiveInt = 3
    max_research_model_calls: PositiveInt = 80
    max_research_tool_calls: NonnegativeInt = 32


class RecoveryFeedback(ResearchModel):
    requirement_id: NonEmpty
    rejected_claim_id: NonEmpty
    reason_code: Literal[
        "evidence_partially_supports_claim", "evidence_does_not_support_claim"
    ]
    explanation: Annotated[NonEmpty, Field(max_length=1200)]
    prior_evidence_ids: list[NonEmpty] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_refs(self):
        if len(set(self.prior_evidence_ids)) != len(self.prior_evidence_ids):
            raise ValueError("Duplicate feedback evidence")
        return self


class RecoveryEligibility(ResearchModel):
    requirement_id: NonEmpty
    paper_id: NonEmpty
    eligible: bool
    reason_code: NonEmpty


class RecoveryTask(ResearchModel):
    recovery_task_id: NonEmpty
    paper_id: NonEmpty
    target_requirement_ids: list[NonEmpty] = Field(min_length=1, max_length=8)
    reviewer_feedback: list[RecoveryFeedback] = Field(min_length=1)
    attempt_index: Annotated[PositiveInt, Field(le=1)] = 1
    max_attempts: Annotated[PositiveInt, Field(le=1)] = 1
    prior_result_refs: list[NonEmpty] = Field(min_length=2)
    prior_worker_result: WorkerResult
    prior_reviews: list[ReviewResult]
    remaining_budget: RecoveryBudget
    review_model_calls_per_requirement: PositiveInt = 3

    @model_validator(mode="after")
    def identities(self):
        targets = self.target_requirement_ids
        if len(set(targets)) != len(targets):
            raise ValueError("Duplicate recovery target")
        worker = self.prior_worker_result
        if worker.paper_id != self.paper_id or worker.worker_task_id != stable_id(
            "worker_", self.paper_id
        ):
            raise ValueError("Recovery worker identity differs")
        requirements = {r.requirement_id: r for r in worker.requirements}
        feedback = {f.requirement_id: f for f in self.reviewer_feedback}
        reviews = {r.requirement_id: r for r in self.prior_reviews}
        if (
            len(feedback) != len(self.reviewer_feedback)
            or len(reviews) != len(self.prior_reviews)
            or set(feedback) != set(targets)
            or set(reviews) != set(targets)
        ):
            raise ValueError(
                "Recovery feedback and review must map once to each target"
            )
        for identity in targets:
            req, fb, review = (
                requirements.get(identity),
                feedback[identity],
                reviews[identity],
            )
            if (
                req is None
                or req.status != "supported"
                or req.paper_id != self.paper_id
            ):
                raise ValueError(
                    "Recovery target must be an original supported requirement"
                )
            if (
                review.status != "rejected"
                or review.validation_status != "passed"
                or review.worker_task_id != worker.worker_task_id
                or review.paper_id != self.paper_id
                or review.claim != req.claim
                or review.evidence_ids != req.evidence_ids
            ):
                raise ValueError("Recovery requires the exact rejected review")
            if (
                fb.reason_code != review.reason_code
                or fb.rejected_claim_id != claim_ref(req)
                or set(fb.prior_evidence_ids) != set(req.evidence_ids)
            ):
                raise ValueError("Feedback does not bind the rejected claim")
            if not set(fb.prior_evidence_ids).issubset(worker.observed_evidence_ids):
                raise ValueError("Feedback references unobserved evidence")
        refs = [
            result_ref("worker", worker),
            *[result_ref("review", r) for r in self.prior_reviews],
        ]
        if self.prior_result_refs != refs:
            raise ValueError("Prior result references do not bind the source snapshots")
        expected_id = stable_id(
            "recovery_",
            self.paper_id,
            *targets,
            *[feedback[i].rejected_claim_id for i in targets],
            "1",
        )
        if self.recovery_task_id != expected_id:
            raise ValueError("Recovery task ID differs from its target and attempt")
        return self

    def worker_task(self):
        originals = {r.requirement_id: r for r in self.prior_worker_result.requirements}
        return WorkerTask(
            worker_task_id=self.prior_worker_result.worker_task_id,
            paper_id=self.paper_id,
            requirements=[
                EvidenceRequirement.model_validate(
                    originals[i].model_dump()
                    | {"status": "pending", "claim": None, "evidence_ids": []}
                )
                for i in self.target_requirement_ids
            ],
        )


class RecoveryHistoryEntry(ResearchModel):
    record_id: NonEmpty
    result_ref: NonEmpty
    kind: Literal["worker", "review"]
    attempt_index: NonnegativeInt
    recorded_at: NonEmpty
    status: NonEmpty
    worker_result: WorkerResult | None = None
    review_result: ReviewResult | None = None


class RecoveryMergeResult(ResearchModel):
    worker_results: list[WorkerResult]
    review_results: list[ReviewResult]
    history: list[RecoveryHistoryEntry]


class RecoveryMetrics(ResearchModel):
    recovery_eligible_count: NonnegativeInt
    recovery_attempted_count: NonnegativeInt
    recovery_passed_count: NonnegativeInt
    recovery_failed_count: NonnegativeInt
    recovery_skipped_count: NonnegativeInt
    recovery_conversion_rate: float | None = Field(default=None, ge=0, le=1)
    additional_model_calls: NonnegativeInt = 0
    additional_tool_calls: NonnegativeInt = 0
    recovery_latency_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    recovery_rounds_used: Annotated[NonnegativeInt, Field(le=1)] = 0
    prior_usage: RecoveryBudget
    reserved_budget: RecoveryBudget
    research_usage: RecoveryBudget

    @model_validator(mode="after")
    def budget_and_outcomes(self):
        if (
            self.recovery_attempted_count > self.recovery_eligible_count
            or self.recovery_attempted_count
            != self.recovery_passed_count + self.recovery_failed_count
        ):
            raise ValueError("Recovery outcome counts differ from attempted targets")
        if self.recovery_rounds_used != int(bool(self.recovery_attempted_count)):
            raise ValueError("Only scheduled targets consume a recovery round")
        if (
            self.additional_model_calls > self.reserved_budget.model_calls
            or self.additional_tool_calls > self.reserved_budget.tool_calls
        ):
            raise ValueError("Recovery costs exceed reserved budget")
        if (
            self.research_usage.model_calls
            != self.prior_usage.model_calls + self.additional_model_calls
            or self.research_usage.tool_calls
            != self.prior_usage.tool_calls + self.additional_tool_calls
        ):
            raise ValueError("Research usage must include declared prior costs")
        return self


class RecoveryOutcome(ResearchModel):
    eligibility: list[RecoveryEligibility]
    tasks: list[RecoveryTask]
    merged: RecoveryMergeResult
    metrics: RecoveryMetrics
    status: Literal["completed", "cancelled"] = "completed"
    synthesis_input: SynthesisInput
    report: ResearchReport | None = None
