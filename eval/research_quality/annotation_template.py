"""输出待填写表单；模板中的空值不是完成的人工审核。"""

from eval.research_quality.schemas import record_sha256


def annotation_template(dataset, predictions=()):
    predicted = {p.case_id: p for p in predictions}
    return {
        "schema_version": 1,
        "purpose": "Human worksheet only. Null placeholders are invalid as completed approvals/assessments.",
        "case_reviews": [
            {
                "case_id": c.case_id,
                "annotation_status": c.annotation_status,
                "question": c.question,
                "reference_answer_candidate": c.reference_answer,
                "source_locations": [
                    s.model_dump(mode="json") for s in c.source_locations
                ],
                "fact_group_ids": c.fact_group_ids,
                "review": {
                    "reviewer": None,
                    "reviewed_at": None,
                    "method": "human_original_pdf_review",
                    "verdict": None,
                    "checked_evidence_ids": [],
                    "checked_boundary_scope": False,
                    "notes": None,
                },
                "proposed_split": None,
            }
            for c in dataset.cases
        ],
        "semantic_assessments": [
            {
                "case_id": c.case_id,
                "case_sha256": record_sha256(c),
                "prediction_sha256": record_sha256(predicted[c.case_id]),
                "assessor": None,
                "assessed_at": None,
                "method": "human_semantic_review",
                "semantic_correct": None,
                "comparison_rubric": {
                    "fact_accuracy": None,
                    "difference_correctness": None,
                    "evidence_grounding": None,
                    "scope_completeness": None,
                }
                if c.comparison
                else None,
                "notes": None,
            }
            for c in dataset.cases
            if c.task_type in {"claim", "comparison"} and c.case_id in predicted
        ],
    }
