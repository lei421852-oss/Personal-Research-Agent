from datetime import UTC, datetime

import pytest
from rq_helpers import gold

from eval.research_quality.annotation_template import annotation_template
from eval.research_quality.dataset_loader import approved_gold, evaluation_input
from eval.research_quality.schemas import (
    ComparisonRubric,
    QualityCase,
    QualityPrediction,
)


def test_ninety_candidates_have_no_automatic_gold(dataset):
    assert len(dataset.cases) == 90
    assert all(c.annotation_status == "pending_review" for c in dataset.cases)
    assert approved_gold(dataset) == []
    assert all(not c.annotation_provenance.human_reviews for c in dataset.cases)


@pytest.mark.parametrize(
    "change",
    [
        "no_review",
        "no_reviewer",
        "no_notes",
        "incomplete_sources",
        "unassigned",
        "rejected_review",
        "model_review",
    ],
)
def test_gold_requires_complete_actual_human_review_contract(dataset, change):
    data = gold(dataset.cases[0]).model_dump(mode="python")
    review = data["annotation_provenance"]["human_reviews"][0]
    if change == "no_review":
        data["annotation_provenance"]["human_reviews"] = []
    elif change == "no_reviewer":
        review["reviewer"] = ""
    elif change == "no_notes":
        review["notes"] = ""
    elif change == "incomplete_sources":
        review["checked_evidence_ids"] = []
    elif change == "unassigned":
        data["split"] = "unassigned"
    elif change == "rejected_review":
        review["verdict"] = "rejected"
    else:
        review["method"] = "llm_review"
    with pytest.raises(ValueError):
        QualityCase.model_validate(data)


def test_gold_boundary_requires_scope_review(dataset):
    case = next(c for c in dataset.cases if c.abstention)
    data = gold(case).model_dump(mode="python")
    data["annotation_provenance"]["human_reviews"][0]["checked_boundary_scope"] = False
    with pytest.raises(ValueError, match="boundary"):
        QualityCase.model_validate(data)


def test_selector_revalidates_model_copy_instead_of_trusting_status(dataset):
    forged = dataset.cases[0].model_copy(
        update={"annotation_status": "approved_gold", "split": "test"}
    )
    changed = dataset.model_copy(update={"cases": [forged]})
    with pytest.raises(ValueError, match="human approval"):
        approved_gold(changed)


def test_rejected_annotation_requires_actual_rejection_record(dataset):
    data = dataset.cases[0].model_dump(mode="python")
    data["annotation_status"] = "rejected"
    with pytest.raises(ValueError, match="human rejection"):
        QualityCase.model_validate(data)
    data = gold(dataset.cases[0]).model_dump(mode="python")
    data["annotation_status"] = "rejected"
    data["annotation_provenance"]["human_reviews"][0]["verdict"] = "rejected"
    assert QualityCase.model_validate(data).annotation_status == "rejected"


def test_naive_human_review_timestamp_is_rejected(dataset):
    data = gold(dataset.cases[0]).model_dump(mode="python")
    data["annotation_provenance"]["human_reviews"][0]["reviewed_at"] = datetime(
        2026, 10, 8, tzinfo=UTC
    ).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone"):
        QualityCase.model_validate(data)


def test_model_output_is_not_allowed_as_reference_origin(dataset):
    data = dataset.cases[0].model_dump(mode="python")
    data["annotation_provenance"]["reference_origin"] = "stage_e_generated_claim"
    with pytest.raises(ValueError):
        QualityCase.model_validate(data)


def test_reviewer_label_and_behavior_are_consistent(dataset):
    case = next(c for c in dataset.cases if c.reviewer)
    data = case.model_dump(mode="python")
    data["reviewer"]["expected_label"] = (
        "rejected" if case.reviewer.expected_label == "passed" else "passed"
    )
    with pytest.raises(ValueError, match="disagree"):
        QualityCase.model_validate(data)


def test_comparison_cannot_have_only_one_paper(dataset):
    data = next(c for c in dataset.cases if c.comparison).model_dump(mode="python")
    data["paper_ids"] = data["paper_ids"][:1]
    with pytest.raises(ValueError):
        QualityCase.model_validate(data)


def test_case_id_task_mismatch_is_rejected(dataset):
    data = dataset.cases[0].model_dump(mode="python")
    data["case_id"] = "rqv1-comparison-wrong-type"
    with pytest.raises(ValueError):
        QualityCase.model_validate(data)


@pytest.mark.parametrize("value", [-1, 3, True, 1.5])
def test_rubric_is_strict_bounded_integer(value):
    with pytest.raises(ValueError):
        ComparisonRubric(
            fact_accuracy=value,
            difference_correctness=2,
            evidence_grounding=2,
            scope_completeness=2,
        )


@pytest.mark.parametrize("label", ["accept", "reject", "approved_gold", "PASSED"])
def test_prediction_labels_follow_exact_contract(label):
    with pytest.raises(ValueError):
        QualityPrediction(
            case_id="case",
            prediction_id="prediction",
            system="test",
            reviewer_label=label,
        )


def test_annotation_template_has_no_completed_approvals(dataset):
    template = annotation_template(dataset)
    assert len(template["case_reviews"]) == 90
    assert all(
        row["review"]["reviewer"] is None
        and row["review"]["verdict"] is None
        and not row["review"]["checked_evidence_ids"]
        for row in template["case_reviews"]
    )
    assert template["semantic_assessments"] == []


def test_model_inputs_do_not_include_reference_labels_or_rationale(dataset):
    for case in dataset.cases:
        payload = evaluation_input(case)
        assert not {
            "reference_answer",
            "gold_evidence_ids",
            "expected_behavior",
            "expected_label",
            "rationale",
            "paper_facts",
            "expected_differences",
        } & set(payload)
        if case.reviewer:
            assert set(payload["review_target"]) == {"claim", "cited_evidence_ids"}
        if case.comparison:
            assert "provided_evidence" not in payload
