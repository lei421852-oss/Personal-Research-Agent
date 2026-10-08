"""只恢复明确审核拒绝且有合法反馈的需求，不用关键词或画像选择事实。"""

from collections import defaultdict

from app.research.models import ResearchPlan, WorkerResult
from app.research.recovery_models import (
    RecoveryEligibility,
    RecoveryFeedback,
    RecoveryInputError,
    claim_ref,
)
from app.research.requirements import expand_requirements, stable_id
from app.research.review_models import ReviewResult, ReviewTask
from app.research.review_validation import DeterministicReviewValidator


def dump(value):
    return (
        value
        if isinstance(value, dict)
        else value.model_dump(mode="python", warnings=False)
    )


def bind_initial(plan, workers, reviews):
    plan = ResearchPlan.model_validate(dump(plan))
    expected = {r.requirement_id: r for r in expand_requirements(plan)}
    bound, review_map = {}, defaultdict(list)
    for raw in workers:
        worker = WorkerResult.model_validate(dump(raw))
        if (
            worker.paper_id not in plan.paper_ids
            or worker.paper_id in bound
            or worker.worker_task_id != stable_id("worker_", worker.paper_id)
        ):
            raise RecoveryInputError("invalid_initial_worker_scope")
        required = {i for i, r in expected.items() if r.paper_id == worker.paper_id}
        if {r.requirement_id for r in worker.requirements} != required:
            raise RecoveryInputError("incomplete_initial_requirements")
        for req in worker.requirements:
            original = expected[req.requirement_id]
            if any(
                getattr(req, f) != getattr(original, f)
                for f in ["paper_id", "dimension_id", "question", "required"]
            ):
                raise RecoveryInputError("changed_initial_requirement")
        bound[worker.paper_id] = worker
    if set(bound) != set(plan.paper_ids):
        raise RecoveryInputError("missing_initial_worker")
    for raw in reviews:
        identity = dump(raw).get("requirement_id")
        if identity not in expected:
            raise RecoveryInputError("out_of_plan_review")
        try:
            review_map[identity].append(ReviewResult.model_validate(dump(raw)))
        except (ValueError, TypeError, AttributeError):
            review_map[identity].append(None)
    return plan, bound, review_map


def bound_review(validator, worker, requirement, records):
    if len(records) != 1 or records[0] is None:
        return None
    review = records[0]
    try:
        valid = validator.validate(worker, requirement)
    except Exception:  # noqa: BLE001 - 证据依赖不可验证时默认拒绝恢复资格
        return None
    if (
        not isinstance(valid, ReviewTask)
        or review.status not in ("passed", "rejected")
        or review.validation_status != "passed"
        or review.worker_task_id != worker.worker_task_id
        or review.paper_id != requirement.paper_id
        or review.claim != requirement.claim
        or review.evidence_ids != requirement.evidence_ids
    ):
        return None
    return review


class RecoveryEligibilityEvaluator:
    def __init__(self, resolver, prepared_paper_ids):
        self.validator = DeterministicReviewValidator(resolver)
        self.prepared_paper_ids = frozenset(prepared_paper_ids)

    def evaluate(
        self,
        plan,
        workers,
        reviews,
        feedback,
        *,
        used_requirement_ids=(),
        task_cancelled=False,
    ):
        plan, bound, review_map = bind_initial(plan, workers, reviews)
        feedback_map = defaultdict(list)
        for raw in feedback:
            try:
                item = RecoveryFeedback.model_validate(dump(raw))
                feedback_map[item.requirement_id].append(item)
            except (ValueError, TypeError, AttributeError):
                identity = (
                    raw.get("requirement_id")
                    if isinstance(raw, dict)
                    else getattr(raw, "requirement_id", None)
                )
                if identity:
                    feedback_map[identity].append(None)
        decisions = []
        for paper in plan.paper_ids:
            worker = bound[paper]
            for req in worker.requirements:
                reason = None
                if task_cancelled:
                    reason = "task_cancelled"
                elif req.status != "supported":
                    reason = req.status.value
                elif req.requirement_id in used_requirement_ids:
                    reason = "recovery_already_used"
                elif paper not in self.prepared_paper_ids:
                    reason = "no_prepared_evidence"
                else:
                    review = bound_review(
                        self.validator, worker, req, review_map[req.requirement_id]
                    )
                    if review is None:
                        reason = "review_unavailable"
                    elif review.status == "passed":
                        reason = "review_passed"
                    else:
                        entries = feedback_map[req.requirement_id]
                        fb = entries[0] if len(entries) == 1 else None
                        if (
                            fb is None
                            or fb.rejected_claim_id != claim_ref(req)
                            or fb.reason_code != review.reason_code
                            or set(fb.prior_evidence_ids) != set(req.evidence_ids)
                        ):
                            reason = "invalid_feedback"
                decisions.append(
                    RecoveryEligibility(
                        requirement_id=req.requirement_id,
                        paper_id=paper,
                        eligible=reason is None,
                        reason_code=reason or "actionable_rejection",
                    )
                )
        return decisions
