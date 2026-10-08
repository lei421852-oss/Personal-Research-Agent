"""计数、混淆矩阵与人工 rubric 汇总；零分母显式返回 null。"""

from collections import defaultdict


def rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def summarize_rows(rows):
    eligible = [r for r in rows if r["eligible_gold"]]
    scored = [r for r in eligible if r["correct"] is not None]
    reviewers = [r for r in eligible if r["task_type"] == "reviewer"]
    labeled = [r for r in reviewers if r.get("predicted_label") is not None]
    tp = sum(r["expected_label"] == r["predicted_label"] == "passed" for r in labeled)
    tn = sum(r["expected_label"] == r["predicted_label"] == "rejected" for r in labeled)
    fp = sum(
        r["expected_label"] == "rejected" and r["predicted_label"] == "passed"
        for r in labeled
    )
    fn = sum(
        r["expected_label"] == "passed" and r["predicted_label"] == "rejected"
        for r in labeled
    )
    abstention = [r for r in scored if r["task_type"] == "abstention"]
    rubrics = [
        r["comparison_rubric"] for r in scored if r.get("comparison_rubric") is not None
    ]
    components = [
        "fact_accuracy",
        "difference_correctness",
        "evidence_grounding",
        "scope_completeness",
    ]
    return {
        "total_cases": len(rows),
        "eligible_gold_cases": len(eligible),
        "excluded_annotation_cases": sum(
            r["status"] == "excluded_annotation" for r in rows
        ),
        "excluded_split_cases": sum(r["status"] == "excluded_split" for r in rows),
        "scored_cases": len(scored),
        "unscored_gold_cases": len(eligible) - len(scored),
        "missing_predictions": sum(
            r["status"] == "missing_prediction" for r in eligible
        ),
        "invalid_predictions": sum(
            r["status"] == "invalid_prediction" for r in eligible
        ),
        "pending_human_assessments": sum(
            r["status"] == "pending_human_assessment" for r in eligible
        ),
        "accuracy": rate(sum(r["correct"] is True for r in scored), len(scored)),
        "assessment_completion": rate(len(scored), len(eligible)),
        "benchmark_complete": bool(eligible) and len(scored) == len(eligible),
        "reviewer": {
            "positive_label": "passed",
            "confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
            "valid_label_predictions": len(labeled),
            "positive_gold_cases": sum(
                r["expected_label"] == "passed" for r in reviewers
            ),
            "negative_gold_cases": sum(
                r["expected_label"] == "rejected" for r in reviewers
            ),
            "label_accuracy": rate(tp + tn, len(labeled)),
            "false_acceptance_rate": rate(fp, fp + tn),
            "false_rejection_rate": rate(fn, fn + tp),
        },
        "abstention_decision_accuracy": rate(
            sum(r["correct"] for r in abstention), len(abstention)
        ),
        "comparison": {
            "human_rubric_cases": len(rubrics),
            "components": {
                key: rate(sum(r[key] for r in rubrics), 2 * len(rubrics))
                for key in components
            },
            "mean_quality_fraction": rate(
                sum(sum(r.values()) for r in rubrics), 8 * len(rubrics)
            ),
        },
    }


def metrics_report(rows):
    papers, dimensions, tasks = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        for paper in row["paper_ids"]:
            papers[paper].append(row)
        dimensions[row["dimension"]].append(row)
        tasks[row["task_type"]].append(row)
    return {
        "overall": summarize_rows(rows),
        "by_paper": {k: summarize_rows(v) for k, v in sorted(papers.items())},
        "by_dimension": {k: summarize_rows(v) for k, v in sorted(dimensions.items())},
        "by_task_type": {k: summarize_rows(v) for k, v in sorted(tasks.items())},
        "group_counting_note": "A cross-paper case is counted once overall and once for each involved paper; paper group denominators are not additive.",
    }
