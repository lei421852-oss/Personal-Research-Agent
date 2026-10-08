"""仅接纳审核通过的原主张；坏来源默认不可用，不让拒绝正文进入事实输入。"""

from collections import defaultdict

from app.research.models import ResearchPlan, WorkerResult
from app.research.requirements import expand_requirements, stable_id
from app.research.review_models import ReviewResult, ReviewTask
from app.research.review_validation import DeterministicReviewValidator
from app.research.synthesis_models import (
    MissingEvidenceStatus,
    SynthesisCell,
    SynthesisInput,
    SynthesisIssue,
    VerifiedClaim,
)


class SynthesisInputError(ValueError):
    def __init__(self, code):
        self.issue = SynthesisIssue(
            code=code,
            message="Research results cannot be mapped unambiguously to the supplied plan.",
        )
        super().__init__(code)


def data(value):
    return (
        value
        if isinstance(value, dict)
        else value.model_dump(mode="python", warnings=False)
    )


class SynthesisInputBuilder:
    def __init__(self, resolver):
        self.validator = DeterministicReviewValidator(resolver)

    def build(self, plan, worker_results, review_results):
        try:
            plan = ResearchPlan.model_validate(data(plan))
        except (ValueError, TypeError, AttributeError) as exc:
            raise SynthesisInputError("invalid_plan") from exc
        expected = {r.requirement_id: r for r in expand_requirements(plan)}
        workers, reviews = defaultdict(list), defaultdict(list)
        for raw in worker_results:
            try:
                record = data(raw)
                paper_id = record["paper_id"]
                if paper_id not in plan.paper_ids:
                    raise SynthesisInputError("out_of_plan_worker")
                if any(
                    r["requirement_id"] not in expected for r in record["requirements"]
                ):
                    raise SynthesisInputError("out_of_plan_requirement")
                workers[paper_id].append(raw)
            except (KeyError, TypeError, AttributeError) as exc:
                raise SynthesisInputError("invalid_worker_input") from exc
        for raw in review_results:
            try:
                identity = data(raw)["requirement_id"]
                if identity not in expected:
                    raise SynthesisInputError("out_of_plan_review")
                reviews[identity].append(raw)
            except (KeyError, TypeError, AttributeError) as exc:
                raise SynthesisInputError("invalid_review_input") from exc
        # 任何重复（即使两个通过结果完全相同）都不能任意挑选一个。
        if any(len(items) > 1 for items in reviews.values()):
            raise SynthesisInputError("duplicate_review")

        cells, verified, missing = [], [], []
        resolved, reviewed = 0, 0

        def unavailable(original, status, code=None):
            issue = (
                SynthesisIssue(
                    code=code,
                    message="This planned cell has no usable reviewed claim.",
                    requirement_id=original.requirement_id,
                )
                if code
                else None
            )
            cells.append(
                SynthesisCell(
                    requirement_id=original.requirement_id,
                    paper_id=original.paper_id,
                    dimension_id=original.dimension_id,
                    status=status,
                )
            )
            missing.append(
                MissingEvidenceStatus(
                    requirement_id=original.requirement_id,
                    paper_id=original.paper_id,
                    dimension_id=original.dimension_id,
                    status=status,
                    issue=issue,
                )
            )

        for original in expected.values():
            candidates = workers[original.paper_id]
            if len(candidates) != 1:
                unavailable(
                    original,
                    "execution_failed",
                    "missing_worker" if not candidates else "duplicate_worker",
                )
                continue
            try:
                worker = WorkerResult.model_validate(data(candidates[0]))
                if worker.worker_task_id != stable_id("worker_", original.paper_id):
                    raise ValueError("Wrong worker identity")
                matches = [
                    r
                    for r in worker.requirements
                    if r.requirement_id == original.requirement_id
                ]
                if len(matches) != 1:
                    raise ValueError("Missing requirement")
                requirement = matches[0]
                for field in ("paper_id", "dimension_id", "question", "required"):
                    if getattr(requirement, field) != getattr(original, field):
                        raise ValueError("Requirement differs from original plan")
            except (ValueError, TypeError, AttributeError):
                unavailable(original, "review_unavailable", "invalid_worker_structure")
                continue
            if requirement.status in ("supported", "insufficient_evidence"):
                resolved += 1
            if requirement.status in (
                "execution_failed",
                "cancelled",
                "insufficient_evidence",
            ):
                unavailable(original, requirement.status.value)
                continue
            if requirement.status != "supported":
                unavailable(original, "execution_failed", "unresolved_requirement")
                continue
            try:
                checked = self.validator.validate(worker, requirement)
            except Exception:  # noqa: BLE001 - 不把证据索引故障转成确认事实
                unavailable(
                    original, "review_unavailable", "evidence_validation_failure"
                )
                continue
            if not isinstance(checked, ReviewTask):
                unavailable(original, "review_unavailable", "invalid_evidence")
                continue
            if not reviews[original.requirement_id]:
                unavailable(original, "review_unavailable", "missing_review")
                continue
            try:
                review = ReviewResult.model_validate(
                    data(reviews[original.requirement_id][0])
                )
                if (
                    review.worker_task_id != worker.worker_task_id
                    or review.paper_id != requirement.paper_id
                    or review.requirement_id != requirement.requirement_id
                    or review.claim != requirement.claim
                    or review.evidence_ids != requirement.evidence_ids
                    or review.validation_status != "passed"
                    or review.status not in ("passed", "rejected")
                ):
                    raise ValueError("Review does not bind the exact supported claim")
            except (ValueError, TypeError, AttributeError):
                unavailable(
                    original, "review_unavailable", "invalid_or_unavailable_review"
                )
                continue
            reviewed += 1
            if review.status == "rejected":
                unavailable(original, "review_rejected")
                continue
            claim = VerifiedClaim(
                claim_id=stable_id(
                    "claim_",
                    requirement.requirement_id,
                    requirement.claim,
                    *requirement.evidence_ids,
                ),
                requirement_id=requirement.requirement_id,
                paper_id=requirement.paper_id,
                dimension_id=requirement.dimension_id,
                text=requirement.claim,
                evidence=checked.evidence,
            )
            verified.append(claim)
            cells.append(
                SynthesisCell(
                    requirement_id=claim.requirement_id,
                    paper_id=claim.paper_id,
                    dimension_id=claim.dimension_id,
                    status="verified",
                    claim_id=claim.claim_id,
                )
            )
        return SynthesisInput(
            plan=plan,
            cells=cells,
            verified_claims=verified,
            missing_evidence=missing,
            allowed_claim_ids=[c.claim_id for c in verified],
            allowed_evidence_ids=list(
                dict.fromkeys(e for c in verified for e in c.evidence_ids)
            ),
            resolved_requirements=resolved,
            reviewed_requirements=reviewed,
        )
