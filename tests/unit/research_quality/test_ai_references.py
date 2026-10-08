"""AI审核完成后的可用性、历史兼容和人工Gold隔离。"""

import json
from collections import Counter, defaultdict

import pytest

from app.research.corpus_validation import canonical_hash
from eval.research_quality.dataset_loader import (
    ROOT,
    approved_gold,
    approved_references,
    load_dataset,
)
from eval.research_quality.schemas import (
    QualityCase,
    QualityDataset,
    QualityPrediction,
    record_sha256,
)
from eval.research_quality.scoring import score_benchmark
from scripts.finalize_quality_references import (
    OUTPUT_DATASET,
    REVIEW_RECORDS,
    SOURCE_DATASET,
    build_reference_dataset,
)


@pytest.fixture(scope="module")
def reference_dataset():
    return load_dataset(OUTPUT_DATASET)


def test_ninety_approved_ai_references_are_ready_and_separate(reference_dataset):
    assert len(reference_dataset.cases) == 90
    assert {c.annotation_status for c in reference_dataset.cases} == {
        "approved_ai_reference"
    }
    assert Counter(c.split for c in reference_dataset.cases) == {"dev": 18, "test": 72}
    assert approved_gold(reference_dataset, split="all") == []
    assert len(approved_references(reference_dataset, split="all")) == 90
    assert len(approved_references(reference_dataset)) == 72
    assert len(approved_references(reference_dataset, split="dev")) == 18
    assert all(
        not c.annotation_provenance.human_reviews for c in reference_dataset.cases
    )


def test_all_shared_fact_components_stay_in_one_split(reference_dataset):
    groups = defaultdict(set)
    for case in reference_dataset.cases:
        for group in case.fact_group_ids:
            groups[group].add(case.split)
    assert len(groups) == 34
    assert all(len(splits) == 1 for splits in groups.values())


def test_approval_is_bound_to_actual_previous_review_record(reference_dataset):
    rows = {
        r["case_id"]: r
        for r in map(json.loads, REVIEW_RECORDS.read_text().splitlines())
    }
    for case in reference_dataset.cases:
        row = rows[case.case_id]
        assert case.ai_review.review_record_sha256 == canonical_hash(row)
        assert case.ai_review.source_case_sha256 == row["reviewed_case_sha256"]
        assert case.ai_review.checked_evidence_ids == row["checked_evidence_ids"]
        assert case.ai_review.notes == row["rationale_zh"]


@pytest.mark.parametrize("field", ["question", "reference_answer", "split"])
def test_reference_edit_requires_new_review_binding(reference_dataset, field):
    raw = reference_dataset.cases[0].model_dump(mode="json")
    raw[field] = "test" if field == "split" else "Changed semantic content"
    with pytest.raises(ValueError, match="content hash"):
        QualityCase.model_validate(raw)


def test_ai_approval_cannot_omit_source_review(reference_dataset):
    raw = reference_dataset.cases[0].model_dump(mode="json")
    raw["ai_review"] = None
    with pytest.raises(ValueError, match="requires source review"):
        QualityCase.model_validate(raw)


def test_ai_boundary_review_must_cover_scope(reference_dataset):
    raw = next(c for c in reference_dataset.cases if c.abstention).model_dump(
        mode="json"
    )
    raw["ai_review"]["checked_boundary_scope"] = False
    with pytest.raises(ValueError, match="boundaries"):
        QualityCase.model_validate(raw)


def test_ai_record_cannot_be_promoted_to_human_gold(reference_dataset):
    raw = reference_dataset.cases[0].model_dump(mode="json")
    raw["annotation_status"] = "approved_gold"
    with pytest.raises(ValueError, match="human approval"):
        QualityCase.model_validate(raw)


def test_manifest_cannot_disguise_ai_reference_as_human(reference_dataset):
    raw = reference_dataset.model_dump(mode="json")
    raw["manifest"]["reference_kind"] = "human_gold"
    with pytest.raises(ValueError, match="ai_reviewed manifest"):
        QualityDataset.model_validate(raw)
    with pytest.raises(ValueError, match="Reference kind"):
        approved_references(reference_dataset, reference_kind="human_gold")


def test_ai_scoring_reports_reference_denominators_and_no_human_gold(reference_dataset):
    case = next(c for c in reference_dataset.cases if c.reviewer and c.split == "test")
    data = reference_dataset.model_copy(update={"cases": [case]})
    prediction = QualityPrediction(
        case_id=case.case_id,
        prediction_id="unit-only",
        system="synthetic",
        reviewer_label=case.reviewer.expected_label,
    )
    result = score_benchmark(data, [prediction])
    assert result["result_scope"] == "provisional_ai_reviewed_reference"
    assert result["approved_ai_reference_cases"] == 1
    assert result["approved_gold_cases"] == 0
    assert result["metrics"]["overall"]["eligible_reference_cases"] == 1
    assert "eligible_gold_cases" not in result["metrics"]["overall"]
    assert result["case_results"][0]["eligible_reference"]
    assert result["metrics"]["overall"]["accuracy"]["value"] == 1


def test_approving_reference_does_not_automatically_grade_open_answer(
    reference_dataset,
):
    case = next(
        c
        for c in reference_dataset.cases
        if c.task_type == "claim" and c.split == "test"
    )
    data = reference_dataset.model_copy(update={"cases": [case]})
    prediction = QualityPrediction(
        case_id=case.case_id,
        prediction_id="unit-only",
        system="synthetic",
        answer=case.reference_answer,
    )
    result = score_benchmark(data, [prediction])
    assert result["case_results"][0]["status"] == "pending_human_assessment"
    assert result["metrics"]["overall"]["accuracy"]["value"] is None


def test_reference_without_predictions_is_explicitly_awaiting(reference_dataset):
    result = score_benchmark(reference_dataset)
    assert result["status"] == "awaiting_predictions"
    assert result["metrics"]["overall"]["eligible_reference_cases"] == 72
    assert result["metrics"]["overall"]["missing_predictions"] == 72


@pytest.mark.parametrize("defect", ["missing", "stale", "false_proof"])
def test_finalization_refuses_missing_or_stale_review(defect):
    dataset = load_dataset(SOURCE_DATASET)
    rows = list(map(json.loads, REVIEW_RECORDS.read_text().splitlines()))
    if defect == "missing":
        rows.pop()
    elif defect == "stale":
        rows[0]["reviewed_case_sha256"] = "0" * 64
    else:
        rows[0]["source_checks"][0]["quote_verified_in_original_pdf_region"] = "true"
    with pytest.raises(ValueError, match="Review"):
        build_reference_dataset(dataset, rows)


def test_old_candidate_case_hashes_remain_compatible():
    dataset = load_dataset(SOURCE_DATASET)
    original = {}
    for entry in dataset.manifest.case_files.values():
        for line in (SOURCE_DATASET / entry.filename).read_text().splitlines():
            raw = json.loads(line)
            original[raw["case_id"]] = canonical_hash(raw)
    assert all(record_sha256(c) == original[c.case_id] for c in dataset.cases)
    assert (
        '"reference_kind"'
        not in (ROOT / "data/benchmark/research_quality_v1/manifest.json").read_text()
    )
