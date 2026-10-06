"""Static validation tests only; no model decisions, retrieval changes or evaluation runner."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evidence.corpus import load_paper_corpus
from eval.loader import DatasetNotReviewedError, load_dataset, load_datasets
from eval.schemas import EvaluationDataset
from eval.validate_dataset import DatasetValidationError, validate_datasets

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def prepared():
    return load_paper_corpus(ROOT / "data/papers")


def test_distribution_provenance_and_approved_review(prepared):
    dev, test = load_datasets()
    report = validate_datasets(dev, test, prepared)
    assert report["total_cases"] == 40 and report["splits"] == {"dev": 10, "test": 30}
    assert report["categories"] == {
        "text_qa": 10,
        "figure_qa": 12,
        "cross_paper": 10,
        "insufficient": 8,
    }
    assert report["review_status"] == {"approved": 40}
    assert report["ground_truth_ready"] is True


def test_candidates_cannot_silently_be_loaded_as_ground_truth(tmp_path):
    data = load_dataset("test").model_dump(mode="json")
    for case in data["cases"]:
        case.update(annotation_status="pending_review", reviewer=None, reviewed_at=None)
    import json

    (tmp_path / "test.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatasetNotReviewedError):
        load_dataset("test", dataset_dir=tmp_path, require_approved=True)


@pytest.mark.parametrize("split", ["../dev", "train", "all"])
def test_loader_rejects_unknown_split(split):
    with pytest.raises(ValueError):
        load_dataset(split)


def test_regression_is_observed_failure_not_desired_behavior():
    case = next(c for c in load_dataset("dev").cases if c.regression)
    assert case.case_id == "dev-text-01"
    assert case.expected_behavior == "answer"
    assert case.regression.observed_status == "step_exhaustion"
    assert case.regression.observed_tool_calls == 6
    assert case.regression.observed_model_calls == 7
    assert all(r.paper_id == "clip-2103.00020v1" for r in case.evidence_references)


def replace_case_id(case, old, new):
    case["relevant_evidence_ids"] = [
        new if i == old else i for i in case["relevant_evidence_ids"]
    ]
    for ref in case["evidence_references"]:
        if ref["evidence_id"] == old:
            ref["evidence_id"] = new
    for point in case["answer_points"]:
        point["supporting_evidence_ids"] = [
            new if i == old else i for i in point["supporting_evidence_ids"]
        ]


@pytest.mark.parametrize(
    "defect,expected",
    [
        ("unknown_paper", "paper_id"),
        ("unknown_evidence", "Unknown evidence_id"),
        ("page", "provenance mismatch"),
        ("figure", "Figure ID"),
        ("target_figure", "Target figure"),
        ("missing_figure", "Figure QA"),
        ("cross_single_paper", "at least two papers"),
        ("hash", "PDF version/hash"),
        ("prepared_pages", "prepared page snapshot"),
        ("count", "40 cases"),
        ("duplicate_id", "Duplicate case_id"),
        ("duplicate_question", "Duplicate question"),
        ("category_counts", "Category counts"),
    ],
)
def test_static_validator_rejects_invalid_corpus_annotations(
    prepared, defect, expected
):
    dev, test = load_datasets()
    data = dev.model_dump(mode="json")
    case = data["cases"][0]
    if defect == "unknown_paper":
        case["paper_ids"] = ["clip-unknown-version"]
    elif defect == "unknown_evidence":
        replace_case_id(case, case["relevant_evidence_ids"][0], "ev_fake")
    elif defect == "page":
        case["evidence_references"][0]["page"] = 99
    elif defect == "figure":
        figure = next(c for c in data["cases"] if c["category"] == "figure_qa")
        next(r for r in figure["evidence_references"] if r["figure_id"])[
            "figure_id"
        ] = "Figure 999"
    elif defect == "target_figure":
        figure = next(c for c in data["cases"] if c["category"] == "figure_qa")
        figure["target_figures"][0]["figure_id"] = "Figure 999"
    elif defect == "missing_figure":
        figure = next(c for c in data["cases"] if c["category"] == "figure_qa")
        text_refs = [r for r in figure["evidence_references"] if r["figure_id"] is None]
        figure["evidence_references"] = text_refs
        figure["relevant_evidence_ids"] = [r["evidence_id"] for r in text_refs]
        for point in figure["answer_points"]:
            point["supporting_evidence_ids"] = figure["relevant_evidence_ids"]
    elif defect == "cross_single_paper":
        cross = next(c for c in data["cases"] if c["category"] == "cross_paper")
        refs = [
            r for r in cross["evidence_references"] if r["paper_id"].startswith("clip")
        ]
        cross["evidence_references"] = refs
        cross["relevant_evidence_ids"] = [r["evidence_id"] for r in refs]
        for point in cross["answer_points"]:
            point["supporting_evidence_ids"] = cross["relevant_evidence_ids"]
    elif defect == "hash":
        data["corpus_snapshot"][0]["pdf_sha256"] = "0" * 64
    elif defect == "prepared_pages":
        data["corpus_snapshot"][0]["prepared_pages"] = [99]
    elif defect == "count":
        data["cases"].pop()
    elif defect == "duplicate_id":
        data["cases"][1]["case_id"] = case["case_id"]
    elif defect == "duplicate_question":
        data["cases"][1]["question"] = case["question"]
    elif defect == "category_counts":
        case["category"] = "cross_paper"
    changed = EvaluationDataset.model_validate(data)
    with pytest.raises(DatasetValidationError, match=expected):
        validate_datasets(changed, test, prepared)


@pytest.mark.parametrize(
    "defect",
    [
        "no_points",
        "point_fake_id",
        "refusal_behavior",
        "refusal_no_forbidden",
        "fake_approval",
    ],
)
def test_schema_rejects_incomplete_annotation(defect):
    data = load_dataset("dev").model_dump(mode="json")
    if defect == "no_points":
        data["cases"][0]["answer_points"] = []
    elif defect == "point_fake_id":
        data["cases"][0]["answer_points"][0]["supporting_evidence_ids"] = ["ev_fake"]
    elif defect == "refusal_behavior":
        next(c for c in data["cases"] if c["category"] == "insufficient")[
            "expected_behavior"
        ] = "answer"
    elif defect == "refusal_no_forbidden":
        next(c for c in data["cases"] if c["category"] == "insufficient")[
            "must_not_claim"
        ] = []
    elif defect == "fake_approval":
        data["cases"][0].update(
            annotation_status="approved", reviewer=None, reviewed_at=None
        )
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(data)


def test_regression_question_is_preserved():
    data = load_dataset("dev").model_dump(mode="json")
    case = next(c for c in data["cases"] if c["regression"])
    case["question"] = "A rewritten question is not the captured regression request."
    with pytest.raises(ValidationError, match="Regression question"):
        EvaluationDataset.model_validate(data)
