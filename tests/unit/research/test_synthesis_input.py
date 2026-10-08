"""研究矩阵和审核身份校验；未通过主张的正文不能进入事实输入。"""

import pytest

from app.evidence.models import TextEvidence
from app.research.models import (
    EvidenceRequirement,
    ResearchDimension,
    ResearchPlan,
    WorkerResult,
)
from app.research.requirements import expand_requirements, group_worker_tasks
from app.research.review_models import ReviewIssue, ReviewResult
from app.research.synthesis_input import SynthesisInputBuilder, SynthesisInputError


class Resolver:
    def __init__(self, evidence):
        self.items = {e.evidence_id: e for e in evidence}

    def resolve(self, identity):
        return self.items[identity]


def bundle(*, statuses=None, rejected=(), unavailable=()):
    plan = ResearchPlan(
        question="Compare the supplied papers using prepared evidence.",
        paper_ids=["paper-a", "paper-b", "paper-c"],
        dimensions=[
            ResearchDimension(
                dimension_id=d,
                name=d.title(),
                description=f"Investigate {d}.",
                research_question=f"What does the paper report about {d}?",
            )
            for d in ["architecture", "training"]
        ],
    )
    tasks = group_worker_tasks(expand_requirements(plan))
    evidence, workers, reviews = [], [], []
    for task in tasks:
        requirements, observed = [], []
        for original in task.requirements:
            key = (original.paper_id, original.dimension_id)
            status = (statuses or {}).get(key, "supported")
            identity = f"ev-{original.paper_id}-{original.dimension_id}"
            text = f"Prepared text for {original.paper_id} {original.dimension_id}."
            evidence.append(
                TextEvidence(
                    evidence_id=identity, paper_id=original.paper_id, page=1, text=text
                )
            )
            item = EvidenceRequirement.model_validate(
                original.model_dump()
                | {
                    "status": status,
                    "claim": f"Authoritative claim {original.paper_id} {original.dimension_id}."
                    if status == "supported"
                    else None,
                    "evidence_ids": [identity] if status == "supported" else [],
                }
            )
            requirements.append(item)
            if status == "supported":
                observed.append(identity)
                review_status = (
                    "not_reviewable"
                    if key in unavailable
                    else "rejected"
                    if key in rejected
                    else "passed"
                )
                reviews.append(
                    ReviewResult(
                        worker_task_id=task.worker_task_id,
                        requirement_id=item.requirement_id,
                        paper_id=item.paper_id,
                        status=review_status,
                        claim=item.claim,
                        evidence_ids=item.evidence_ids,
                        validation_status="passed",
                        reason_code=None
                        if review_status == "not_reviewable"
                        else "evidence_does_not_support_claim"
                        if review_status == "rejected"
                        else "evidence_supports_claim",
                        issue=ReviewIssue(
                            code="dependency_failure",
                            message="Reviewer provider failed.",
                        )
                        if review_status == "not_reviewable"
                        else None,
                    )
                )
        resolved = sum(
            r.status in ["supported", "insufficient_evidence"] for r in requirements
        )
        worker_status = (
            "completed"
            if resolved == len(requirements)
            else "partial_success"
            if resolved
            else "cancelled"
            if any(r.status == "cancelled" for r in requirements)
            else "failed"
        )
        workers.append(
            WorkerResult(
                worker_task_id=task.worker_task_id,
                paper_id=task.paper_id,
                status=worker_status,
                requirements=requirements,
                observed_evidence_ids=observed,
            )
        )
    return plan, workers, reviews, Resolver(evidence)


def source(**kwargs):
    plan, workers, reviews, resolver = bundle(**kwargs)
    return SynthesisInputBuilder(resolver).build(plan, workers, reviews)


def proposal(input_data):
    claims = {c.claim_id: c for c in input_data.verified_claims}
    return {
        "title": "Evidence-grounded research report",
        "sections": [
            {
                "dimension_id": d.dimension_id,
                "title": "Verified findings",
                "cells": [
                    c.model_dump(mode="json")
                    | {
                        "evidence_ids": claims[c.claim_id].evidence_ids
                        if c.claim_id
                        else []
                    }
                    for c in input_data.cells
                    if c.dimension_id == d.dimension_id
                ],
                "comparisons": [],
            }
            for d in input_data.plan.dimensions
        ],
    }


def test_all_supported_and_passed_yield_exact_verified_matrix():
    value = source()
    assert len(value.cells) == len(value.verified_claims) == 6
    assert value.missing_evidence == []
    assert len(value.allowed_claim_ids) == len(value.allowed_evidence_ids) == 6
    assert value.resolved_requirements == value.reviewed_requirements == 6


def test_partial_papers_keep_all_missing_cells_and_no_unverified_claims():
    statuses = {
        (p, d): "insufficient_evidence"
        for p in ["paper-b", "paper-c"]
        for d in ["architecture", "training"]
    }
    value = source(statuses=statuses)
    assert len(value.cells) == 6 and len(value.verified_claims) == 2
    assert {c.paper_id for c in value.verified_claims} == {"paper-a"}
    assert len(value.missing_evidence) == 4
    assert all(m.status == "insufficient_evidence" for m in value.missing_evidence)


def test_rejected_claim_body_is_absent_from_entire_synthesis_context():
    value = source(rejected={("paper-a", "architecture")})
    assert "Authoritative claim paper-a architecture." not in value.model_dump_json()
    assert len(value.verified_claims) == 5
    assert value.missing_evidence[0].status == "review_rejected"


def test_reviewer_execution_failure_remains_unavailable_not_rejected():
    value = source(unavailable={("paper-a", "architecture")})
    assert value.missing_evidence[0].status == "review_unavailable"
    assert value.reviewed_requirements == 5


@pytest.mark.parametrize(
    "status", ["insufficient_evidence", "execution_failed", "cancelled"]
)
def test_worker_missing_statuses_are_preserved(status):
    value = source(statuses={("paper-a", "architecture"): status})
    assert value.missing_evidence[0].status == status


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_review_is_structured_error_even_if_identical(conflict):
    plan, workers, reviews, resolver = bundle()
    duplicate = (
        reviews[0].model_copy(
            update={
                "status": "rejected",
                "reason_code": "evidence_does_not_support_claim",
            }
        )
        if conflict
        else reviews[0]
    )
    with pytest.raises(SynthesisInputError) as error:
        SynthesisInputBuilder(resolver).build(plan, workers, reviews + [duplicate])
    assert error.value.issue.code == "duplicate_review"


@pytest.mark.parametrize(
    "field,value",
    [
        ("paper_id", "paper-b"),
        ("claim", "Changed unreviewed claim."),
        ("worker_task_id", "wrong-worker"),
        ("evidence_ids", ["unknown-evidence"]),
    ],
)
def test_mismatched_review_fails_closed(field, value):
    plan, workers, reviews, resolver = bundle()
    reviews[0] = reviews[0].model_copy(update={field: value})
    result = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    assert result.cells[0].status == "review_unavailable"
    assert result.cells[0].claim_id is None


def test_missing_review_and_unknown_corpus_evidence_are_unavailable():
    plan, workers, reviews, resolver = bundle()
    del resolver.items[workers[0].requirements[1].evidence_ids[0]]
    value = SynthesisInputBuilder(resolver).build(plan, workers, reviews[1:])
    assert value.cells[0].status == value.cells[1].status == "review_unavailable"
    assert len(value.verified_claims) == 4


def test_plan_requirement_identity_and_question_cannot_be_rewritten():
    plan, workers, reviews, resolver = bundle()
    changed = (
        workers[0]
        .requirements[0]
        .model_copy(update={"question": "Different research scope"})
    )
    workers[0] = workers[0].model_copy(
        update={"requirements": [changed, workers[0].requirements[1]]}
    )
    value = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    assert value.cells[0].status == "review_unavailable"


def test_out_of_plan_review_is_rejected_before_factual_input_exists():
    plan, workers, reviews, resolver = bundle()
    reviews.append(reviews[0].model_copy(update={"requirement_id": "fake-requirement"}))
    with pytest.raises(SynthesisInputError, match="out_of_plan_review"):
        SynthesisInputBuilder(resolver).build(plan, workers, reviews)


def test_unobserved_evidence_cannot_become_verified():
    plan, workers, reviews, resolver = bundle()
    workers[0] = workers[0].model_copy(update={"observed_evidence_ids": []})
    value = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    assert all(c.status == "review_unavailable" for c in value.cells[:2])


def test_duplicate_worker_results_are_unavailable_without_selecting_a_success():
    plan, workers, reviews, resolver = bundle()
    value = SynthesisInputBuilder(resolver).build(plan, workers + [workers[0]], reviews)
    assert all(c.status == "execution_failed" for c in value.cells[:2])
    assert len(value.verified_claims) == 4
