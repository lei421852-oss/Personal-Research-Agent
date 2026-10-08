"""标签可确定性计分，开放语义必须有绑定到具体预测的人工评分。"""

from eval.research_quality.dataset_loader import approved_gold
from eval.research_quality.metrics import metrics_report
from eval.research_quality.schemas import (
    HumanAssessment,
    QualityDataset,
    QualityPrediction,
    record_sha256,
)


class ScoringInputError(ValueError):
    pass


def keyed(records, model):
    result = {}
    for raw in records:
        checked = model.model_validate(
            raw.model_dump() if isinstance(raw, model) else raw
        )
        if checked.case_id in result:
            raise ScoringInputError("duplicate_prediction_or_assessment")
        result[checked.case_id] = checked
    return result


def score_benchmark(dataset, predictions=(), assessments=(), *, split="test"):
    dataset = QualityDataset.model_validate(dataset.model_dump(mode="python"))
    selected = {c.case_id for c in approved_gold(dataset, split=split)}
    cases = {c.case_id: c for c in dataset.cases}
    if len(cases) != len(dataset.cases):
        raise ScoringInputError("duplicate_case_id")
    # 纯评分接口也检查分组隔离；不依赖调用者先运行 CLI 校验。
    group_splits = {}
    for case in dataset.cases:
        if case.usage_partition != "benchmark_only":
            raise ScoringInputError("optimization_data_in_benchmark")
        if case.split != "unassigned":
            for group in case.fact_group_ids:
                if group in group_splits and group_splits[group] != case.split:
                    raise ScoringInputError("split_leakage")
                group_splits[group] = case.split
    predicted = keyed(predictions, QualityPrediction)
    human = keyed(assessments, HumanAssessment)
    if not set(predicted) <= set(cases) or not set(human) <= set(cases):
        raise ScoringInputError("unknown_case_id")
    for identity, assessment in human.items():
        case, prediction = cases[identity], predicted.get(identity)
        if (
            prediction is None
            or assessment.case_sha256 != record_sha256(case)
            or assessment.prediction_sha256 != record_sha256(prediction)
        ):
            raise ScoringInputError("human_assessment_snapshot_mismatch")
        if case.task_type not in {"claim", "comparison"} or (
            assessment.comparison_rubric is not None
        ) != (case.task_type == "comparison"):
            raise ScoringInputError("human_assessment_wrong_task_or_rubric")
        if (
            not prediction.answer or prediction.abstained is True
        ) and assessment.semantic_correct:
            raise ScoringInputError("human_correct_without_answer")
    rows = []
    for case in dataset.cases:
        row = {
            "case_id": case.case_id,
            "task_type": case.task_type,
            "paper_ids": case.paper_ids,
            "dimension": case.dimension,
            "eligible_gold": case.case_id in selected,
            "status": "excluded_split"
            if case.annotation_status == "approved_gold"
            else "excluded_annotation",
            "correct": None,
            "score_source": None,
            "expected_label": case.reviewer.expected_label if case.reviewer else None,
            "predicted_label": None,
            "comparison_rubric": None,
        }
        rows.append(row)
        if not row["eligible_gold"]:
            continue
        prediction = predicted.get(case.case_id)
        if prediction is None:
            row.update(
                status="missing_prediction",
                correct=False,
                score_source="operational_failure",
            )
        elif case.reviewer:
            if prediction.reviewer_label is None:
                row.update(
                    status="invalid_prediction",
                    correct=False,
                    score_source="operational_failure",
                )
            else:
                row.update(
                    status="scored",
                    correct=prediction.reviewer_label == case.reviewer.expected_label,
                    predicted_label=prediction.reviewer_label,
                    score_source="structured_label_comparison",
                )
        elif case.abstention:
            if prediction.abstained is None:
                row.update(
                    status="invalid_prediction",
                    correct=False,
                    score_source="operational_failure",
                )
            else:
                row.update(
                    status="scored",
                    correct=prediction.abstained == case.abstention.expected_abstention,
                    score_source="structured_abstention_decision",
                )
        elif not prediction.answer or prediction.abstained is True:
            row.update(
                status="invalid_prediction",
                correct=False,
                score_source="operational_failure",
            )
        elif case.case_id not in human:
            row.update(status="pending_human_assessment")
        else:
            assessment = human[case.case_id]
            row.update(
                status="scored",
                correct=assessment.semantic_correct,
                score_source="human_semantic_review",
                comparison_rubric=assessment.comparison_rubric.model_dump()
                if assessment.comparison_rubric
                else None,
            )
    metrics = metrics_report(rows)
    return {
        "status": (
            "not_scored_no_gold_in_selected_split"
            if any(c.annotation_status == "approved_gold" for c in dataset.cases)
            else "not_scored_no_approved_gold"
        )
        if not selected
        else "scored"
        if metrics["overall"]["benchmark_complete"]
        else "partial_pending_human_assessment",
        "dataset_version": dataset.manifest.dataset_version,
        "split": split,
        "candidate_cases": sum(
            c.annotation_status in {"draft", "pending_review"} for c in dataset.cases
        ),
        "human_reviewed_cases": sum(
            bool(c.annotation_provenance.human_reviews) for c in dataset.cases
        ),
        "approved_gold_cases": sum(
            c.annotation_status == "approved_gold" for c in dataset.cases
        ),
        "metrics": metrics,
        "case_results": rows,
        "protocol": {
            "open_answer_semantics": "Human assessment bound to case and prediction hashes; no string-match correctness.",
            "accuracy_denominator": "Cases with a known correctness value. Missing/invalid predictions are failures; ungraded open answers are reported separately, not silently treated as correct or incorrect.",
            "reviewer_rates": "Confusion matrix and false-accept/reject denominators use valid label predictions only; missing/invalid predictions remain explicit operational failures in overall/task accuracy.",
            "abstention": "Structured decision accuracy, not automatic semantic judgment of refusal prose.",
            "comparison_rubric": "Four human axes, each 0..2; aggregate fractions do not infer correctness from a threshold.",
        },
        "real_provider_calls": 0,
    }
