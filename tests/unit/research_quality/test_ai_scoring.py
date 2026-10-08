"""AI核对来源必须显式，不能替代人工记录或伪造预测正确性。"""

from datetime import UTC, datetime

import pytest
from rq_helpers import assessment, gold

from eval.research_quality.dataset_loader import load_dataset
from eval.research_quality.schemas import (
    AIAssessment,
    HumanAssessment,
    QualityPrediction,
    record_sha256,
)
from eval.research_quality.scoring import ScoringInputError, score_benchmark
from scripts.finalize_quality_references import OUTPUT_DATASET


def fixture():
    dataset = load_dataset(OUTPUT_DATASET)
    case = next(
        c for c in dataset.cases if c.task_type == "claim" and c.split == "test"
    )
    prediction = QualityPrediction(
        case_id=case.case_id,
        prediction_id="unit-only",
        system="synthetic",
        answer=case.reference_answer,
    )
    ai = AIAssessment(
        case_id=case.case_id,
        case_sha256=record_sha256(case),
        prediction_sha256=record_sha256(prediction),
        assessor="synthetic-ai-assessor",
        assessed_at=datetime.now(UTC),
        method="ai_semantic_review",
        semantic_correct=False,
        notes="Synthetic unit fixture only.",
    )
    return dataset.model_copy(update={"cases": [case]}), case, prediction, ai


def test_ai_semantic_grade_is_explicit_and_not_exact_string_match():
    dataset, _, prediction, ai = fixture()
    result = score_benchmark(dataset, [prediction], ai_assessments=[ai])
    assert result["metrics"]["overall"]["accuracy"]["value"] == 0
    assert result["case_results"][0]["score_source"] == "ai_semantic_review"
    assert (
        result["human_semantic_assessment_count"] == 0
        and result["ai_semantic_assessment_count"] == 1
    )


def test_ai_cannot_enter_human_record_contract():
    _, _, _, ai = fixture()
    with pytest.raises(ValueError):
        HumanAssessment.model_validate(ai.model_dump())


def test_ai_grade_cannot_enter_independent_human_gold_scope(dataset):
    case = gold(dataset.cases[0])
    prediction = QualityPrediction(
        case_id=case.case_id, prediction_id="unit", system="synthetic", answer="Text"
    )
    ai = AIAssessment(
        case_id=case.case_id,
        case_sha256=record_sha256(case),
        prediction_sha256=record_sha256(prediction),
        assessor="synthetic-ai",
        assessed_at=datetime.now(UTC),
        method="ai_semantic_review",
        semantic_correct=True,
        notes="Unit fixture",
    )
    with pytest.raises(ScoringInputError, match="ai_reference_scope"):
        score_benchmark(
            dataset.model_copy(update={"cases": [case]}),
            [prediction],
            ai_assessments=[ai],
        )


def test_mixed_reviewers_for_same_answer_are_not_silently_selected():
    dataset, case, prediction, ai = fixture()
    with pytest.raises(ScoringInputError, match="conflicting"):
        score_benchmark(
            dataset, [prediction], [assessment(case, prediction)], ai_assessments=[ai]
        )


def test_ai_snapshot_and_rubric_checks_remain_enforced():
    dataset, _, prediction, ai = fixture()
    with pytest.raises(ScoringInputError, match="snapshot"):
        score_benchmark(
            dataset,
            [prediction.model_copy(update={"answer": "Changed"})],
            ai_assessments=[ai],
        )
