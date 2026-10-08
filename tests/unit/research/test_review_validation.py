"""语义审查之前，确定性检查只确认结构、观察账本和同论文证据身份。"""

import pytest
from test_paper_worker import EV, FIGURE, PAPER, Resolver, task

from app.research.models import EvidenceRequirement, WorkerResult, WorkerTrace
from app.research.review_models import ReviewResult, ReviewTask
from app.research.review_validation import DeterministicReviewValidator


def requirement(*, status="supported", evidence_ids=None):
    original = task().requirements[0]
    return EvidenceRequirement.model_validate(
        original.model_dump()
        | {
            "status": status,
            "claim": "Synthetic original paper-local claim"
            if status == "supported"
            else None,
            "evidence_ids": [EV.evidence_id]
            if status == "supported" and evidence_ids is None
            else evidence_ids or [],
        }
    )


def worker_result(item=None, *, observed_ids=None):
    item = item or requirement()
    status = {"execution_failed": "failed", "cancelled": "cancelled"}.get(
        item.status, "completed"
    )
    return WorkerResult(
        worker_task_id=task().worker_task_id,
        paper_id=PAPER,
        status=status,
        requirements=[item],
        observed_evidence_ids=[EV.evidence_id]
        if observed_ids is None
        else observed_ids,
    )


def validated_task():
    item = requirement()
    result = DeterministicReviewValidator(Resolver()).validate(
        worker_result(item), item
    )
    assert isinstance(result, ReviewTask)
    return result


def test_supported_requirement_with_observed_real_same_paper_evidence_becomes_task():
    source = worker_result()
    item = source.requirements[0]
    prepared = DeterministicReviewValidator(Resolver()).validate(source, item)
    assert isinstance(prepared, ReviewTask)
    assert prepared.worker_task_id == source.worker_task_id
    assert prepared.requirement == item
    assert [e.evidence_id for e in prepared.evidence] == item.evidence_ids
    assert prepared.evidence[0].text == EV.text
    assert (
        prepared.evidence[0].paper_id == PAPER and prepared.evidence[0].page == EV.page
    )


def test_figure_review_task_uses_caption_and_provenance_without_filesystem_path():
    item = requirement(evidence_ids=[FIGURE.evidence_id])
    source = worker_result(item, observed_ids=[FIGURE.evidence_id])
    prepared = DeterministicReviewValidator(Resolver((EV, FIGURE))).validate(
        source, item
    )
    assert isinstance(prepared, ReviewTask)
    evidence = prepared.evidence[0]
    assert evidence.caption == FIGURE.caption and evidence.figure_id == FIGURE.figure_id
    assert evidence.modality == "figure" and evidence.text == FIGURE.text
    assert "asset_path" not in evidence.model_dump()
    assert "/tmp/private" not in prepared.model_dump_json()


def test_legal_insufficient_evidence_is_a_valid_skip_not_a_rejection():
    item = requirement(status="insufficient_evidence")
    result = DeterministicReviewValidator(Resolver()).validate(
        worker_result(item), item
    )
    assert isinstance(result, ReviewResult)
    assert result.status == "not_reviewable"
    assert result.validation_status == "passed"
    assert result.skip_reason == "insufficient_evidence"
    assert result.reason_code is None and result.issue is None
    assert result.claim is None and result.evidence_ids == []
    assert result.trace.model_calls == result.trace.retry_count == 0


@pytest.mark.parametrize("status", ["execution_failed", "cancelled"])
def test_execution_failure_and_cancelled_requirements_skip_semantic_review(status):
    item = requirement(status=status)
    result = DeterministicReviewValidator(Resolver()).validate(
        worker_result(item), item
    )
    assert isinstance(result, ReviewResult)
    assert result.status == "not_reviewable" and result.validation_status == "skipped"
    assert result.skip_reason == status
    assert result.reason_code is None and result.issue is None
    assert result.trace.model_calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"claim": None},
        {"evidence_ids": []},
        {"evidence_ids": [EV.evidence_id, EV.evidence_id]},
        {
            "status": "insufficient_evidence",
            "claim": "Invalid claim in a skip",
            "evidence_ids": [],
        },
    ],
)
def test_model_construct_cannot_bypass_requirement_invariants(changes):
    item = EvidenceRequirement.model_construct(**(requirement().model_dump() | changes))
    source = WorkerResult.model_construct(
        **(worker_result().model_dump() | {"requirements": [item]})
    )
    result = DeterministicReviewValidator(Resolver()).validate(source, item)
    assert isinstance(result, ReviewResult)
    assert result.status == "not_reviewable" and result.validation_status == "rejected"
    assert result.reason_code == "invalid_structure" and result.issue is not None
    assert result.trace.model_calls == 0


def test_unknown_corpus_evidence_is_rejected_despite_presence_in_observed_ledger():
    item = requirement(evidence_ids=["ev-absent"])
    source = worker_result(item, observed_ids=["ev-absent"])
    result = DeterministicReviewValidator(Resolver()).validate(source, item)
    assert isinstance(result, ReviewResult)
    assert (
        result.validation_status == "rejected"
        and result.reason_code == "evidence_missing"
    )
    assert result.trace.model_calls == 0


def test_evidence_from_another_paper_is_rejected():
    foreign = EV.model_copy(
        update={"evidence_id": "ev-foreign", "paper_id": "paper-other"}
    )
    item = requirement(evidence_ids=[foreign.evidence_id])
    source = worker_result(item, observed_ids=[foreign.evidence_id])
    result = DeterministicReviewValidator(Resolver((EV, foreign))).validate(
        source, item
    )
    assert isinstance(result, ReviewResult)
    assert (
        result.validation_status == "rejected"
        and result.reason_code == "invalid_structure"
    )
    assert result.trace.model_calls == 0


def test_existing_but_unobserved_evidence_cannot_enter_semantic_review():
    item = requirement()
    result = DeterministicReviewValidator(Resolver()).validate(
        worker_result(item, observed_ids=[]), item
    )
    assert isinstance(result, ReviewResult)
    assert (
        result.validation_status == "rejected"
        and result.reason_code == "invalid_structure"
    )
    assert result.trace.model_calls == 0


def test_trace_or_final_citations_do_not_replace_the_runtime_observed_ledger():
    item = requirement()
    source = worker_result(item, observed_ids=[])
    source = source.model_copy(
        update={
            "trace": WorkerTrace(
                worker_task_id=source.worker_task_id,
                paper_id=PAPER,
                status="completed",
                requirement_count=1,
                resolved_count=1,
                tool_calls=1,
                actions=[{"evidence_ids": [EV.evidence_id], "status": "success"}],
            )
        }
    )
    # 最终引用已经含该 ID；仍不可倒推它曾经由 Tool 返回。
    assert item.evidence_ids == [EV.evidence_id]
    result = DeterministicReviewValidator(Resolver()).validate(source, item)
    assert isinstance(result, ReviewResult)
    assert result.validation_status == "rejected" and result.trace.model_calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"requirement_id": "another-requirement"},
        {"paper_id": "another-paper"},
        {"claim": "A rewritten claim not produced by this Worker"},
        {"question": "A replacement research question"},
    ],
)
def test_external_requirement_cannot_change_original_worker_identity_or_claim(changes):
    source = worker_result()
    external = source.requirements[0].model_copy(update=changes)
    result = DeterministicReviewValidator(Resolver()).validate(source, external)
    assert isinstance(result, ReviewResult)
    assert (
        result.validation_status == "rejected"
        and result.reason_code == "invalid_structure"
    )
    assert result.trace.model_calls == 0
