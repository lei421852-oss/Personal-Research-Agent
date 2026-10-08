"""合成审批/评分仅用于确定性测试，不是人工审核证明。"""

from datetime import UTC, datetime

from eval.research_quality.schemas import HumanAssessment, QualityCase, record_sha256


def gold(case, *, split="test"):
    data = case.model_dump(mode="python")
    data.update(annotation_status="approved_gold", split=split)
    data["annotation_provenance"]["human_reviews"] = [
        {
            "reviewer": "synthetic-test-human",
            "reviewed_at": datetime(2026, 10, 8, 12, tzinfo=UTC),
            "method": "human_original_pdf_review",
            "verdict": "approved",
            "checked_evidence_ids": case.gold_evidence_ids,
            "checked_boundary_scope": case.abstention is not None,
            "notes": "Synthetic fixture only, not actual annotation.",
        }
    ]
    return QualityCase.model_validate(data)


def assessment(case, prediction, *, correct=True, rubric=None):
    return HumanAssessment(
        case_id=case.case_id,
        case_sha256=record_sha256(case),
        prediction_sha256=record_sha256(prediction),
        assessor="synthetic-test-assessor",
        assessed_at=datetime(2026, 10, 8, 12, tzinfo=UTC),
        method="human_semantic_review",
        semantic_correct=correct,
        comparison_rubric=rubric,
        notes="Synthetic deterministic scoring fixture, not a real human result.",
    )
