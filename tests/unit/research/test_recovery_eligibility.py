"""资格、反馈身份和一次恢复预算的确定性契约。"""

import pytest
from test_synthesis_input import bundle

from app.research.recovery_eligibility import RecoveryEligibilityEvaluator
from app.research.recovery_models import (
    RecoveryBudget,
    RecoveryConfig,
    RecoveryFeedback,
    RecoveryTask,
    claim_ref,
    result_ref,
)
from app.research.requirements import stable_id


def initial(*, rejected=None, statuses=None, unavailable=()):
    rejected = {("paper-a", "architecture")} if rejected is None else rejected
    plan, workers, reviews, resolver = bundle(
        rejected=rejected, statuses=statuses, unavailable=unavailable
    )
    feedback = []
    for worker in workers:
        for req in worker.requirements:
            match = next(
                (
                    r
                    for r in reviews
                    if r.requirement_id == req.requirement_id and r.status == "rejected"
                ),
                None,
            )
            if match:
                feedback.append(
                    RecoveryFeedback(
                        requirement_id=req.requirement_id,
                        rejected_claim_id=claim_ref(req),
                        reason_code=match.reason_code,
                        explanation="Narrow the claim to directly supported details or locate evidence for the unsupported part.",
                        prior_evidence_ids=req.evidence_ids,
                    )
                )
    return plan, workers, reviews, resolver, feedback


def recovery_task(*, target_indices=(0,), tools=2, models=6):
    rejected = {
        ("paper-a", d)
        for i, d in enumerate(["architecture", "training"])
        if i in target_indices
    }
    plan, workers, reviews, resolver, feedback = initial(rejected=rejected)
    worker = workers[0]
    ids = [worker.requirements[i].requirement_id for i in target_indices]
    selected_reviews = [r for r in reviews if r.requirement_id in ids]
    selected_feedback = [f for f in feedback if f.requirement_id in ids]
    task = RecoveryTask(
        recovery_task_id=stable_id(
            "recovery_",
            worker.paper_id,
            *ids,
            *[f.rejected_claim_id for f in selected_feedback],
            "1",
        ),
        paper_id=worker.paper_id,
        target_requirement_ids=ids,
        reviewer_feedback=selected_feedback,
        prior_worker_result=worker,
        prior_reviews=selected_reviews,
        prior_result_refs=[
            result_ref("worker", worker),
            *[result_ref("review", r) for r in selected_reviews],
        ],
        remaining_budget=RecoveryBudget(model_calls=models, tool_calls=tools),
    )
    return task, plan, workers, reviews, resolver, feedback


def evaluate(**kwargs):
    plan, workers, reviews, resolver, feedback = initial(**kwargs)
    return RecoveryEligibilityEvaluator(resolver, plan.paper_ids).evaluate(
        plan, workers, reviews, feedback
    )


def test_rejected_actionable_feedback_is_eligible_and_passed_is_skipped():
    decisions = evaluate()
    assert decisions[0].eligible
    assert all(
        not d.eligible and d.reason_code == "review_passed" for d in decisions[1:]
    )


@pytest.mark.parametrize(
    "status", ["insufficient_evidence", "execution_failed", "cancelled"]
)
def test_non_rejected_worker_states_skip(status):
    decisions = evaluate(rejected=set(), statuses={("paper-a", "architecture"): status})
    assert not decisions[0].eligible and decisions[0].reason_code == status


def test_unavailable_review_is_not_semantic_rejection():
    decisions = evaluate(rejected=set(), unavailable={("paper-a", "architecture")})
    assert (
        not decisions[0].eligible and decisions[0].reason_code == "review_unavailable"
    )


@pytest.mark.parametrize(
    "mode",
    ["empty", "missing", "wrong_claim", "wrong_evidence", "wrong_reason", "duplicate"],
)
def test_invalid_feedback_cannot_create_recovery(mode):
    plan, workers, reviews, resolver, feedback = initial()
    if mode == "missing":
        feedback = []
    elif mode == "duplicate":
        feedback = feedback * 2
    else:
        updates = {
            "empty": {"explanation": " "},
            "wrong_claim": {"rejected_claim_id": "claim-fake"},
            "wrong_evidence": {"prior_evidence_ids": ["ev-fake"]},
            "wrong_reason": {"reason_code": "evidence_partially_supports_claim"},
        }[mode]
        feedback = [feedback[0].model_copy(update=updates)]
    decisions = RecoveryEligibilityEvaluator(resolver, plan.paper_ids).evaluate(
        plan, workers, reviews, feedback
    )
    assert not decisions[0].eligible and decisions[0].reason_code == "invalid_feedback"


def test_no_prepared_evidence_used_or_original_cancelled_skip():
    plan, workers, reviews, resolver, feedback = initial()
    evaluator = RecoveryEligibilityEvaluator(resolver, [])
    assert (
        evaluator.evaluate(plan, workers, reviews, feedback)[0].reason_code
        == "no_prepared_evidence"
    )
    evaluator = RecoveryEligibilityEvaluator(resolver, plan.paper_ids)
    assert (
        evaluator.evaluate(
            plan,
            workers,
            reviews,
            feedback,
            used_requirement_ids=[feedback[0].requirement_id],
        )[0].reason_code
        == "recovery_already_used"
    )
    assert all(
        d.reason_code == "task_cancelled"
        for d in evaluator.evaluate(
            plan, workers, reviews, feedback, task_cancelled=True
        )
    )


def test_null_feedback_is_skipped_without_an_exception():
    plan, workers, reviews, resolver, _ = initial()
    decisions = RecoveryEligibilityEvaluator(resolver, plan.paper_ids).evaluate(
        plan, workers, reviews, [None]
    )
    assert not decisions[0].eligible and decisions[0].reason_code == "invalid_feedback"


@pytest.mark.parametrize(
    "field,value",
    [
        ("paper_id", "paper-other"),
        ("target_requirement_ids", ["req-fake"]),
        ("prior_result_refs", ["fake", "fake"]),
        ("attempt_index", 2),
        ("max_attempts", 2),
    ],
)
def test_recovery_task_identity_rejects_tampering(field, value):
    task, *_ = recovery_task()
    with pytest.raises(ValueError):
        RecoveryTask.model_validate(task.model_dump() | {field: value})


@pytest.mark.parametrize("value", [0, 2, True, "1"])
def test_round_configuration_is_exactly_one_strict_integer(value):
    with pytest.raises(ValueError):
        RecoveryConfig(max_recovery_rounds=value)
