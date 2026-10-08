import json
import shutil

import pytest
from rq_helpers import gold

from eval.research_quality.dataset_loader import DEFAULT_DATASET, load_dataset
from eval.research_quality.dataset_validator import (
    BenchmarkValidationError,
    validate_dataset,
)
from eval.research_quality.schemas import QualityCase
from scripts.prepare_quality_benchmark import build_candidates


def replaced(dataset, case):
    return dataset.model_copy(
        update={
            "cases": [case if c.case_id == case.case_id else c for c in dataset.cases]
        }
    )


def test_real_source_locations_all_validate(dataset, sources):
    report = validate_dataset(dataset, *sources)
    assert report["task_counts"] == {
        "claim": 30,
        "reviewer": 30,
        "comparison": 15,
        "abstention": 15,
    }
    assert report["human_reviewed_count"] == report["approved_gold_count"] == 0
    assert report["fact_group_count"] == 34


def test_unknown_paper_is_rejected(dataset, sources):
    data = dataset.cases[0].model_dump(mode="python")
    data["paper_ids"] = ["ofa-2202.03052v1"]
    case = QualityCase.model_validate(data)
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(replaced(dataset, case), *sources)
    assert any("unknown_paper" in issue for issue in error.value.issues)


def test_unknown_evidence_id_is_rejected(dataset, sources):
    data = dataset.cases[0].model_dump(mode="python")
    data["gold_evidence_ids"] = ["ev_fake"]
    data["source_locations"][0]["evidence_id"] = "ev_fake"
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)
    assert any("unknown_evidence" in issue for issue in error.value.issues)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pdf_page", 99),
        ("quote", "Invented source quotation"),
        ("source_document_sha256", "0" * 64),
        ("source_version", "v99"),
    ],
)
def test_source_provenance_changes_are_rejected(dataset, sources, field, value):
    data = dataset.cases[0].model_dump(mode="python")
    data["source_locations"][0][field] = value
    with pytest.raises(BenchmarkValidationError):
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)


def test_duplicate_case_is_not_silently_deduplicated(dataset, sources):
    bad = dataset.model_copy(update={"cases": [*dataset.cases, dataset.cases[0]]})
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(bad, *sources)
    assert "duplicate_case_id" in error.value.issues


def test_comparison_sources_cannot_be_assigned_to_wrong_paper(dataset, sources):
    data = next(c for c in dataset.cases if c.comparison).model_dump(mode="python")
    facts = data["comparison"]["paper_facts"]
    facts[0]["evidence_ids"], facts[1]["evidence_ids"] = (
        facts[1]["evidence_ids"],
        facts[0]["evidence_ids"],
    )
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)
    assert any(
        "comparison_citation_wrong_paper" in issue for issue in error.value.issues
    )


def test_difference_needs_sources_for_both_parties(dataset, sources):
    data = next(
        c
        for c in dataset.cases
        if c.comparison and len(c.comparison.paper_facts[0].evidence_ids) >= 2
    ).model_dump(mode="python")
    data["comparison"]["expected_differences"][0]["evidence_ids"] = data["comparison"][
        "paper_facts"
    ][0]["evidence_ids"]
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)
    assert any(
        "difference_missing_party_evidence" in issue for issue in error.value.issues
    )


def test_same_fact_group_cannot_cross_dev_test(dataset, sources):
    selected = [
        c
        for c in dataset.cases
        if c.reviewer and c.fact_group_ids == ["clip-symmetric-loss"]
    ]
    first, second = gold(selected[0], split="dev"), gold(selected[1], split="test")
    bad = replaced(replaced(dataset, first), second)
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(bad, *sources)
    assert any("split_leakage" in issue for issue in error.value.issues)


def test_optimization_partition_and_group_usage_leak_are_rejected(dataset, sources):
    data = dataset.cases[0].model_dump(mode="python")
    data["usage_partition"] = "model_optimization"
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)
    assert any("usage_leakage" in issue for issue in error.value.issues)


def test_identical_reference_rewrite_requires_shared_group(dataset, sources):
    case = next(c for c in dataset.cases if c.reviewer)
    data = case.model_dump(mode="python")
    data["fact_group_ids"] = ["unrelated-group"]
    with pytest.raises(BenchmarkValidationError) as error:
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)
    assert any(
        "identical_reference_without_shared_group" in issue
        for issue in error.value.issues
    )


def test_false_unprepared_page_marker_is_rejected(dataset, sources):
    data = next(
        c
        for c in dataset.cases
        if c.abstention and c.abstention.boundary_kind == "unprepared_page"
    ).model_dump(mode="python")
    data["abstention"]["requested_page"] = 2
    with pytest.raises(BenchmarkValidationError):
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)


def test_caption_only_boundary_cannot_claim_full_corpus_access(dataset, sources):
    data = next(
        c
        for c in dataset.cases
        if c.abstention and c.abstention.boundary_kind == "caption_only"
    ).model_dump(mode="python")
    data["abstention"]["assessment_scope"] = "prepared_corpus_only"
    with pytest.raises(BenchmarkValidationError):
        validate_dataset(replaced(dataset, QualityCase.model_validate(data)), *sources)


def test_loader_detects_case_file_hash_changes(tmp_path):
    shutil.copytree(DEFAULT_DATASET, tmp_path / "dataset")
    file = tmp_path / "dataset/claim_cases.jsonl"
    file.write_text(file.read_text() + "\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_dataset(tmp_path / "dataset")


def test_loader_rejects_manifest_path_escape(tmp_path):
    shutil.copytree(DEFAULT_DATASET, tmp_path / "dataset")
    path = tmp_path / "dataset/manifest.json"
    data = json.loads(path.read_text())
    data["case_files"]["claim"]["filename"] = "../outside.jsonl"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="escapes"):
        load_dataset(tmp_path / "dataset")


def test_candidate_preparation_is_repeatable_for_same_source_and_timestamp(
    dataset, sources
):
    first = build_candidates(*sources, dataset.manifest.prepared_at)
    second = build_candidates(*sources, dataset.manifest.prepared_at)
    assert first == second
    assert {c.case_id: c for c in first} == {c.case_id: c for c in dataset.cases}
