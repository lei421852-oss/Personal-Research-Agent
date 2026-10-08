import pytest
from rq_helpers import assessment, gold

from eval.research_quality.schemas import QualityPrediction
from eval.research_quality.scoring import ScoringInputError, score_benchmark


def subset(dataset, cases):
    return dataset.model_copy(update={"cases": cases})


def prediction(case, **kwargs):
    return QualityPrediction(
        case_id=case.case_id,
        prediction_id="synthetic-prediction",
        system="synthetic-unit-policy",
        **kwargs,
    )


def test_candidates_cannot_contribute_to_formal_accuracy(dataset):
    result = score_benchmark(dataset)
    assert result["status"] == "not_scored_no_approved_gold"
    assert result["metrics"]["overall"]["accuracy"] == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }
    assert result["candidate_cases"] == 90 and result["approved_gold_cases"] == 0


def test_empty_scoring_input_has_no_invented_accuracy(dataset):
    result = score_benchmark(subset(dataset, []))
    metrics = result["metrics"]["overall"]
    assert result["status"] == "not_scored_no_approved_gold"
    assert metrics["total_cases"] == 0
    assert metrics["accuracy"]["denominator"] == 0
    assert metrics["accuracy"]["value"] is None
    assert not metrics["benchmark_complete"]


def test_exact_reference_string_still_requires_human_semantic_assessment(dataset):
    case = gold(dataset.cases[0])
    result = score_benchmark(
        subset(dataset, [case]), [prediction(case, answer=case.reference_answer)]
    )
    assert result["case_results"][0]["correct"] is None
    assert result["case_results"][0]["status"] == "pending_human_assessment"
    assert result["metrics"]["overall"]["accuracy"]["denominator"] == 0


def test_human_approved_paraphrase_can_score_without_string_match(dataset):
    case = gold(dataset.cases[0])
    pred = prediction(
        case,
        answer="The contrastive score matrix is supervised in both directions using cross entropy.",
    )
    result = score_benchmark(subset(dataset, [case]), [pred], [assessment(case, pred)])
    assert result["case_results"][0]["correct"] is True
    assert result["case_results"][0]["score_source"] == "human_semantic_review"


@pytest.mark.parametrize("changed", ["prediction", "case"])
def test_old_assessment_cannot_score_changed_snapshot(dataset, changed):
    case = gold(dataset.cases[0])
    pred = prediction(case, answer="Original answer")
    human = assessment(case, pred)
    if changed == "prediction":
        pred = pred.model_copy(update={"answer": "Different answer"})
    else:
        case = case.model_copy(update={"reference_answer": "Changed reference"})
    with pytest.raises(ScoringInputError, match="snapshot"):
        score_benchmark(subset(dataset, [case]), [pred], [human])


def test_reviewer_confusion_matrix_and_error_denominators(dataset):
    originals = [c for c in dataset.cases if c.reviewer][:4]
    cases = [gold(c) for c in originals]
    preds = [
        prediction(c, reviewer_label=p)
        for c, p in zip(
            cases, ["passed", "passed", "rejected", "rejected"], strict=True
        )
    ]
    result = score_benchmark(subset(dataset, cases), preds)
    reviewer = result["metrics"]["overall"]["reviewer"]
    assert reviewer["confusion_matrix"] == {"tp": 1, "tn": 1, "fp": 1, "fn": 1}
    assert reviewer["false_acceptance_rate"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert reviewer["false_rejection_rate"]["value"] == 0.5


def test_missing_predictions_are_failures_without_fabricated_confusion_labels(dataset):
    case = gold(next(c for c in dataset.cases if c.reviewer))
    result = score_benchmark(subset(dataset, [case]))
    metrics = result["metrics"]["overall"]
    assert metrics["accuracy"] == {"numerator": 0, "denominator": 1, "value": 0}
    assert metrics["missing_predictions"] == 1
    assert metrics["reviewer"]["confusion_matrix"] == {
        "tp": 0,
        "tn": 0,
        "fp": 0,
        "fn": 0,
    }
    assert metrics["reviewer"]["false_rejection_rate"]["value"] is None


@pytest.mark.parametrize("task", ["claim", "reviewer", "comparison", "abstention"])
def test_incomplete_prediction_payload_is_explicit_failure(dataset, task):
    case = gold(next(c for c in dataset.cases if c.task_type == task))
    result = score_benchmark(subset(dataset, [case]), [prediction(case)])
    assert result["case_results"][0]["status"] == "invalid_prediction"
    assert result["metrics"]["overall"]["accuracy"]["value"] == 0


def test_refusal_text_is_not_used_as_a_keyword_classifier(dataset):
    case = gold(
        next(
            c
            for c in dataset.cases
            if c.abstention and c.abstention.expected_abstention
        )
    )
    result = score_benchmark(
        subset(dataset, [case]),
        [prediction(case, answer="I cannot answer with this evidence.")],
    )
    assert result["case_results"][0]["status"] == "invalid_prediction"
    result = score_benchmark(
        subset(dataset, [case]), [prediction(case, abstained=True)]
    )
    assert result["metrics"]["overall"]["abstention_decision_accuracy"]["value"] == 1


def test_answerable_control_penalizes_unnecessary_abstention(dataset):
    case = gold(
        next(
            c
            for c in dataset.cases
            if c.abstention and not c.abstention.expected_abstention
        )
    )
    result = score_benchmark(
        subset(dataset, [case]), [prediction(case, abstained=True)]
    )
    assert result["case_results"][0]["correct"] is False


def test_comparison_uses_human_axes_without_automatic_correctness_threshold(dataset):
    case = gold(next(c for c in dataset.cases if c.comparison))
    pred = prediction(
        case, answer="A comparison whose correctness is judged by a human."
    )
    rubric = {
        "fact_accuracy": 2,
        "difference_correctness": 1,
        "evidence_grounding": 2,
        "scope_completeness": 1,
    }
    result = score_benchmark(
        subset(dataset, [case]),
        [pred],
        [assessment(case, pred, correct=False, rubric=rubric)],
    )
    metrics = result["metrics"]["overall"]
    assert metrics["accuracy"]["value"] == 0
    assert metrics["comparison"]["mean_quality_fraction"]["value"] == 0.75
    assert metrics["comparison"]["components"]["difference_correctness"]["value"] == 0.5


def test_comparison_needs_complete_rubric(dataset):
    case = gold(next(c for c in dataset.cases if c.comparison))
    pred = prediction(case, answer="comparison")
    with pytest.raises(ScoringInputError, match="rubric"):
        score_benchmark(subset(dataset, [case]), [pred], [assessment(case, pred)])


@pytest.mark.parametrize("kind", ["predictions", "assessments"])
def test_duplicate_results_are_not_arbitrarily_selected(dataset, kind):
    case = gold(dataset.cases[0])
    pred = prediction(case, answer="answer")
    human = assessment(case, pred)
    with pytest.raises(ScoringInputError, match="duplicate"):
        score_benchmark(
            subset(dataset, [case]),
            [pred, pred] if kind == "predictions" else [pred],
            [human, human] if kind == "assessments" else [],
        )


def test_unknown_prediction_id_and_invalid_human_task_fail_explicitly(dataset):
    with pytest.raises(ScoringInputError, match="unknown"):
        score_benchmark(
            dataset,
            [QualityPrediction(case_id="unknown", prediction_id="p", system="test")],
        )
    case = gold(next(c for c in dataset.cases if c.reviewer))
    pred = prediction(case, reviewer_label="passed")
    with pytest.raises(ScoringInputError, match="wrong_task"):
        score_benchmark(subset(dataset, [case]), [pred], [assessment(case, pred)])


def test_human_cannot_mark_empty_answer_correct(dataset):
    case = gold(dataset.cases[0])
    pred = prediction(case)
    with pytest.raises(ScoringInputError, match="without_answer"):
        score_benchmark(subset(dataset, [case]), [pred], [assessment(case, pred)])


def test_scoring_reexecution_is_deterministic_and_does_not_mutate_inputs(dataset):
    case = gold(dataset.cases[0])
    pred = prediction(case, answer="A human-graded paraphrase.")
    human = assessment(case, pred)
    data = subset(dataset, [case])
    before = data.model_dump_json()
    assert score_benchmark(data, [pred], [human]) == score_benchmark(
        data, [pred], [human]
    )
    assert data.model_dump_json() == before


def test_dev_gold_is_excluded_from_default_test_denominator(dataset):
    case = gold(dataset.cases[0], split="dev")
    result = score_benchmark(subset(dataset, [case]))
    assert result["status"] == "not_scored_no_gold_in_selected_split"
    metrics = result["metrics"]["overall"]
    assert metrics["excluded_split_cases"] == 1
    assert metrics["excluded_annotation_cases"] == 0
    assert metrics["eligible_gold_cases"] == 0


def test_pure_scoring_api_also_rejects_partition_leakage(dataset):
    cases = [
        c
        for c in dataset.cases
        if c.reviewer and c.fact_group_ids == ["clip-symmetric-loss"]
    ]
    with pytest.raises(ScoringInputError, match="split_leakage"):
        score_benchmark(
            subset(dataset, [gold(cases[0], split="dev"), gold(cases[1], split="test")])
        )
