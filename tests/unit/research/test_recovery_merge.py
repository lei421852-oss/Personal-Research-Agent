"""审核复核后才替换主张，失败/不足与历史审核拒绝不混淆。"""

import pytest
from test_recovery_eligibility import recovery_task

from app.research.models import EvidenceRequirement, WorkerResult
from app.research.recovery_merge import RecoveryResultMerger
from app.research.review_models import ReviewIssue, ReviewResult
from app.research.synthesis_input import SynthesisInputBuilder


def recovered(task, *, status="supported", review_status="passed", evidence_id=None):
    req = task.worker_task().requirements[0]
    ids = [evidence_id] if evidence_id else task.reviewer_feedback[0].prior_evidence_ids
    req = EvidenceRequirement.model_validate(
        req.model_dump()
        | {
            "status": status,
            "claim": "Narrowed recovery claim." if status == "supported" else None,
            "evidence_ids": ids if status == "supported" else [],
        }
    )
    worker = WorkerResult(
        worker_task_id=task.prior_worker_result.worker_task_id,
        paper_id=task.paper_id,
        status="completed"
        if status in ["supported", "insufficient_evidence"]
        else "cancelled"
        if status == "cancelled"
        else "failed",
        requirements=[req],
        observed_evidence_ids=ids,
    )
    reviews = []
    if status == "supported":
        reviews = [
            ReviewResult(
                worker_task_id=worker.worker_task_id,
                requirement_id=req.requirement_id,
                paper_id=req.paper_id,
                status=review_status,
                claim=req.claim,
                evidence_ids=req.evidence_ids,
                validation_status="passed",
                reason_code="evidence_supports_claim"
                if review_status == "passed"
                else "evidence_does_not_support_claim"
                if review_status == "rejected"
                else None,
                issue=ReviewIssue(
                    code="dependency_failure", message="Reviewer unavailable."
                )
                if review_status == "not_reviewable"
                else None,
            )
        ]
    return worker, reviews


def merge_case(**kwargs):
    task, plan, workers, reviews, resolver, _ = recovery_task()
    result, new_reviews = recovered(task, **kwargs)
    merged = RecoveryResultMerger(resolver).merge(
        plan, workers, reviews, [task], [result], new_reviews
    )
    source = SynthesisInputBuilder(resolver).build(
        plan, merged.worker_results, merged.review_results
    )
    return merged, source, task, workers, reviews


def test_new_passed_claim_is_verified_and_initial_approved_is_exactly_preserved():
    merged, source, _task, workers, reviews = merge_case()
    assert source.cells[0].status == "verified"
    assert source.verified_claims[0].text == "Narrowed recovery claim."
    assert merged.worker_results[0].requirements[1] == workers[0].requirements[1]
    assert merged.review_results[1] == reviews[1]
    assert len({r.record_id for r in merged.history}) == len(merged.history)
    assert {r.attempt_index for r in merged.history} == {0, 1}
    assert all(r.recorded_at and r.result_ref and r.status for r in merged.history)


@pytest.mark.parametrize(
    "review_status,expected",
    [("rejected", "review_rejected"), ("not_reviewable", "review_unavailable")],
)
def test_new_claim_needs_re_review_and_terminal_rejection_is_not_insufficient(
    review_status, expected
):
    _merged, source, *_ = merge_case(review_status=review_status)
    assert source.cells[0].status == expected
    assert "Narrowed recovery claim." not in source.model_dump_json()
    assert len(source.verified_claims) == 5


def test_missing_re_review_never_promotes_supported():
    task, plan, workers, reviews, resolver, _ = recovery_task()
    result, _ = recovered(task)
    merged = RecoveryResultMerger(resolver).merge(
        plan, workers, reviews, [task], [result], []
    )
    source = SynthesisInputBuilder(resolver).build(
        plan, merged.worker_results, merged.review_results
    )
    assert source.cells[0].status == "review_unavailable"


def test_recovery_insufficient_is_history_not_relabelling_prior_rejection():
    merged, source, *_ = merge_case(status="insufficient_evidence")
    assert source.cells[0].status == "review_rejected"
    assert (
        merged.history[-1].worker_result.requirements[0].status
        == "insufficient_evidence"
    )
    assert len(source.verified_claims) == 5


@pytest.mark.parametrize("status", ["execution_failed", "cancelled"])
def test_execution_failure_and_cancellation_stay_distinct(status):
    _, source, *_ = merge_case(status=status)
    assert source.cells[0].status == status


@pytest.mark.parametrize("identity", ["ev-fake", "ev-paper-b-architecture"])
def test_unknown_or_wrong_paper_evidence_cannot_become_verified(identity):
    _, source, *_ = merge_case(evidence_id=identity)
    assert source.cells[0].status == "review_unavailable"
    assert len(source.verified_claims) == 5


def test_duplicate_re_review_fail_closed_and_history_retains_both_records():
    task, plan, workers, reviews, resolver, _ = recovery_task()
    result, new_reviews = recovered(task)
    conflict = new_reviews[0].model_copy(
        update={"status": "rejected", "reason_code": "evidence_does_not_support_claim"}
    )
    merged = RecoveryResultMerger(resolver).merge(
        plan, workers, reviews, [task], [result], new_reviews + [conflict]
    )
    source = SynthesisInputBuilder(resolver).build(
        plan, merged.worker_results, merged.review_results
    )
    assert source.cells[0].status == "review_unavailable"
    assert (
        len([h for h in merged.history if h.attempt_index == 1 and h.kind == "review"])
        == 2
    )
    assert len({r.requirement_id for r in merged.review_results}) == 6


def test_duplicate_initial_review_keeps_other_approved_facts():
    _task, plan, workers, reviews, resolver, _ = recovery_task()
    merged = RecoveryResultMerger(resolver).merge(plan, workers, reviews + [reviews[0]])
    source = SynthesisInputBuilder(resolver).build(
        plan, merged.worker_results, merged.review_results
    )
    assert (
        source.cells[0].status == "review_unavailable"
        and len(source.verified_claims) == 5
    )


def test_direct_restoration_of_identical_rejected_claim_is_not_verified():
    task, plan, workers, reviews, resolver, _ = recovery_task()
    result, new_reviews = recovered(task)
    old = workers[0].requirements[0]
    result = result.model_copy(update={"requirements": [old]})
    new_reviews = [new_reviews[0].model_copy(update={"claim": old.claim})]
    merged = RecoveryResultMerger(resolver).merge(
        plan, workers, reviews, [task], [result], new_reviews
    )
    source = SynthesisInputBuilder(resolver).build(
        plan, merged.worker_results, merged.review_results
    )
    assert source.cells[0].status == "review_unavailable"
