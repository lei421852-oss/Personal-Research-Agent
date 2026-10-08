from eval.research_quality.metrics import metrics_report, rate, summarize_rows


def row(task="reviewer", truth="passed", pred="passed", correct=True, papers=None):
    return {
        "case_id": "synthetic",
        "task_type": task,
        "paper_ids": papers or ["paper-a"],
        "dimension": "architecture",
        "eligible_gold": True,
        "status": "scored",
        "correct": correct,
        "expected_label": truth,
        "predicted_label": pred,
        "comparison_rubric": None,
    }


def test_zero_denominators_are_null_with_counts():
    assert rate(0, 0) == {"numerator": 0, "denominator": 0, "value": None}
    result = summarize_rows([])
    assert result["accuracy"]["value"] is None
    assert result["reviewer"]["false_acceptance_rate"]["denominator"] == 0
    assert not result["benchmark_complete"]


def test_positive_only_reviewer_set_has_undefined_false_acceptance_rate():
    result = summarize_rows([row(), row(pred="rejected", correct=False)])
    assert result["reviewer"]["false_acceptance_rate"]["value"] is None
    assert result["reviewer"]["false_rejection_rate"]["value"] == 0.5


def test_negative_only_reviewer_set_has_undefined_false_rejection_rate():
    result = summarize_rows(
        [
            row(truth="rejected", pred="rejected"),
            row(truth="rejected", pred="passed", correct=False),
        ]
    )
    assert result["reviewer"]["false_rejection_rate"]["value"] is None
    assert result["reviewer"]["false_acceptance_rate"]["value"] == 0.5


def test_pending_human_score_is_visible_and_not_fabricated():
    pending = row(task="claim", truth=None, pred=None, correct=None) | {
        "status": "pending_human_assessment"
    }
    result = summarize_rows([pending, row()])
    assert result["accuracy"]["denominator"] == 1
    assert result["eligible_gold_cases"] == 2
    assert result["unscored_gold_cases"] == result["pending_human_assessments"] == 1
    assert result["assessment_completion"]["value"] == 0.5
    assert not result["benchmark_complete"]


def test_cross_paper_case_is_counted_once_overall_and_per_each_paper():
    data = [
        row(task="comparison", truth=None, pred=None, papers=["paper-a", "paper-b"])
    ]
    metrics = metrics_report(data)
    assert metrics["overall"]["accuracy"]["denominator"] == 1
    assert all(m["accuracy"]["denominator"] == 1 for m in metrics["by_paper"].values())
    assert metrics["by_task_type"]["comparison"]["scored_cases"] == 1
    assert metrics["by_dimension"]["architecture"]["scored_cases"] == 1


def test_missing_negative_prediction_does_not_inflate_false_acceptance_denominator():
    missing = row(truth="rejected", pred=None, correct=False) | {
        "status": "missing_prediction"
    }
    result = summarize_rows(
        [missing, row(truth="rejected", pred="passed", correct=False)]
    )
    assert result["reviewer"]["negative_gold_cases"] == 2
    assert result["reviewer"]["false_acceptance_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1,
    }
    assert result["missing_predictions"] == 1 and result["accuracy"]["denominator"] == 2
