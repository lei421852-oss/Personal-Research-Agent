"""独立覆盖对照评分的分母、身份、失败分类与历史标签保护。"""

from copy import deepcopy

import pytest

from eval.research_quality.coverage_experiment_metrics import (
    F2_RUBRIC_FIELDS,
    SIX_DIMENSIONS,
    build_comparison,
)


def execution(case_id="case-a", *, model_calls=10, tool_calls=3):
    workers = []
    reviews = []
    diagnostics = []
    for paper in ("paper-a", "paper-b"):
        worker_id, requirement_id = f"worker-{paper}", f"req-{paper}"
        workers.append(
            {
                "worker_task_id": worker_id,
                "paper_id": paper,
                "status": "completed",
                "error": None,
                "requirements": [
                    {"requirement_id": requirement_id, "status": "supported"}
                ],
            }
        )
        reviews.append({"status": "passed", "issue": None})
        diagnostics.append(
            {
                "worker_task_id": worker_id,
                "paper_id": paper,
                "attempt_index": 0,
                "report": {
                    "paper_id": paper,
                    "requirements": [
                        {
                            "requirement_id": requirement_id,
                            "complete": True,
                            "coverage": [
                                {"aspect_id": "model_family", "status": "covered"}
                            ],
                            "stages": [],
                        }
                    ],
                    "metrics": {
                        "coverage_rejections": 0,
                        "additional_model_calls": 0,
                        "additional_tool_calls": 0,
                    },
                },
            }
        )
    return {
        "case_id": case_id,
        "prediction": {"case_id": case_id, "answer": "Synthetic reviewed facts."},
        "provider_attempts": model_calls,
        "detail": {
            "initial_worker_results": workers,
            "initial_review_results": reviews,
            "outcome": {
                "merged": {"worker_results": workers, "review_results": reviews},
                "report": {"status": "completed", "generation_degraded": False},
            },
            "metrics": {
                "total_model_calls": model_calls,
                "total_tool_calls": tool_calls,
                "wall_time_ms": 2000.0,
            },
            "coverage_diagnostics": diagnostics,
        },
    }


def assessment(case_id="case-a", **updates):
    return {
        "case_id": case_id,
        "method": "ai_semantic_review",
        "assessor": "test-ai",
        "notes": "Synthetic independent review; no human Gold claim.",
        "original_f2_semantic_correct": False,
        "semantic_correct": True,
        "six_dimension_scores": {
            key: None if key == "abstention_reasonableness" else 2
            for key in SIX_DIMENSIONS
        },
        "comparison_rubric": dict.fromkeys(F2_RUBRIC_FIELDS, 2),
        "change_class": "improved",
        "change_reason": "Model families are now present and cited.",
        "unsupported_additional_assertions": 0,
        "evidence_boundary_violations": 0,
        "premature_supported": 0,
        "semantic_complete_requirements": 2,
        "assessed_requirements": 2,
        **updates,
    }


def compare(new=None, scored=None, old=None):
    return build_comparison(
        [old or execution()],
        [new or execution(model_calls=12, tool_calls=4)],
        [scored or assessment()],
    )


def test_original_label_and_independent_treatment_are_separate():
    rows, summary = compare()
    assert rows[0]["original_f2_semantic_correct"] is False
    assert summary["original_f2_quality"] == {
        "numerator": 0,
        "denominator": 1,
        "value": 0,
    }
    assert summary["treatment_quality"]["value"] == 1
    assert summary["change_counts"]["improved"] == 1
    assert rows[0]["cost_change"]["model_calls"] == {"delta": 2, "ratio": 1.2}
    assert summary["cost_change"]["tool_calls"]["delta"] == 1


def test_empty_inputs_do_not_create_scores_or_costs():
    rows, summary = build_comparison([], [], [])
    assert rows == []
    assert summary["treatment_quality"]["value"] is None
    assert summary["structured_complete_requirements"]["denominator"] == 0
    assert summary["treatment_cost"]["model_calls"]["total"] is None
    assert summary["cost_change"]["tool_calls"]["delta"] is None


@pytest.mark.parametrize("kind", ["baseline", "treatment", "assessment"])
def test_duplicate_case_is_rejected(kind):
    old, new, scored = [execution()], [execution()], [assessment()]
    {"baseline": old, "treatment": new, "assessment": scored}[kind].append(
        deepcopy({"baseline": old, "treatment": new, "assessment": scored}[kind][0])
    )
    with pytest.raises(ValueError, match=f"duplicate_{kind}"):
        build_comparison(old, new, scored)


@pytest.mark.parametrize("kind", ["treatment", "assessment"])
def test_unknown_case_cannot_be_added_to_diagnostic_set(kind):
    new = execution("unknown") if kind == "treatment" else execution()
    scored = assessment("unknown") if kind == "assessment" else assessment()
    with pytest.raises(ValueError, match="unknown_comparison_case"):
        build_comparison([execution()], [new], [scored])


def test_missing_treatment_and_prediction_are_visible():
    rows, summary = build_comparison([execution()], [], [])
    assert rows[0]["change_class"] == "not_comparable"
    assert summary["operational_counts"]["missing_treatment_executions"] == 1
    assert summary["treatment_quality"]["denominator"] == 0
    new = execution()
    new.pop("prediction")
    rows, summary = compare(new)
    assert summary["operational_counts"]["missing_predictions"] == 1
    assert rows[0]["semantic_scoring_eligible"] is False


def test_cost_missing_values_are_null_not_zero_or_fake_savings():
    new = execution()
    new["detail"]["metrics"] = {}
    new.pop("provider_attempts")
    rows, summary = compare(new)
    assert all(value is None for value in rows[0]["treatment_cost"].values())
    assert summary["treatment_cost"]["model_calls"]["missing_cases"] == 1
    assert summary["cost_change"]["model_calls"]["paired_cases"] == 0
    assert summary["cost_change"]["latency_ms"]["ratio"] is None


def test_top_level_cost_aliases_and_zero_baseline_are_supported():
    old, new = execution(model_calls=0, tool_calls=0), execution()
    new["detail"].pop("metrics")
    new["metrics"] = {"model_calls": 1, "tool_calls": 0, "latency_ms": 4}
    rows, _ = compare(new, old=old)
    assert rows[0]["cost_change"]["model_calls"]["delta"] == 1
    assert rows[0]["cost_change"]["model_calls"]["ratio"] is None


@pytest.mark.parametrize("value", [True, -1, float("nan"), 1.5])
def test_invalid_count_cost_is_rejected(value):
    new = execution()
    new["detail"]["metrics"]["total_model_calls"] = value
    with pytest.raises(ValueError, match="invalid_nonnegative_metric"):
        compare(new)


def test_provider_failure_is_not_directly_scored_as_research_quality_failure():
    new = execution()
    worker = new["detail"]["outcome"]["merged"]["worker_results"][0]
    worker["status"] = "failed"
    worker["error"] = {
        "code": "dependency_failure",
        "details": {"error_type": "APIConnectionError"},
    }
    worker["requirements"][0]["status"] = "execution_failed"
    rows, summary = compare(new)
    assert rows[0]["change_class"] == "not_comparable"
    assert rows[0]["treatment_semantics"]["semantic_correct"] is True
    assert summary["treatment_quality"]["denominator"] == 0
    assert summary["operational_counts"]["infrastructure_failure_cases"] == 1


def test_recovered_initial_failure_does_not_hide_successful_final_state():
    new = execution()
    initial = deepcopy(new["detail"]["initial_worker_results"])
    initial[0]["error"] = {"code": "dependency_failure"}
    initial[0]["status"] = "failed"
    new["detail"]["initial_worker_results"] = initial
    rows, summary = compare(new)
    assert rows[0]["treatment_execution"]["infra_failure"] is False
    assert summary["treatment_quality"]["denominator"] == 1


def test_execution_failed_is_not_called_reasonable_abstention():
    new = execution()
    worker = new["detail"]["outcome"]["merged"]["worker_results"][0]
    worker["status"] = "failed"
    worker["error"] = {"code": "model_budget_exhausted"}
    worker["requirements"][0]["status"] = "execution_failed"
    _, summary = compare(
        new, assessment(semantic_correct=False, change_class="degraded")
    )
    assert summary["operational_counts"]["execution_failed_cases"] == 1
    assert summary["operational_counts"]["new_insufficient_requirements"] == 0


def test_insufficient_result_is_not_automatically_a_semantic_success():
    new = execution()
    new["detail"]["initial_worker_results"][0]["requirements"][0]["status"] = (
        "insufficient_evidence"
    )
    rows, summary = compare(new, assessment(semantic_correct=False))
    assert summary["treatment_quality"]["value"] == 0
    assert rows[0]["treatment_execution"]["insufficient_requirement_count"] == 1


def test_declared_coverage_and_reviewer_pass_do_not_prove_semantic_completeness():
    rows, summary = compare(
        scored=assessment(semantic_correct=False, semantic_complete_requirements=1)
    )
    assert rows[0]["structured_coverage"]["complete_requirement_rate"]["value"] == 1
    assert (
        summary["treatment_semantics"]["independent_semantic_completeness"]["value"]
        == 0.5
    )
    assert summary["treatment_quality"]["value"] == 0


def test_absent_coverage_uses_authoritative_denominator_instead_of_inflated_one_of_one():
    new = execution()
    new["detail"]["coverage_diagnostics"].pop()
    rows, summary = compare(new)
    assert rows[0]["structured_coverage"]["complete_requirement_rate"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert summary["missing_coverage_requirement_count"] == 1


def test_latest_attempt_coverage_is_used_but_all_attempt_guard_costs_survive():
    new = execution()
    diagnostics = new["detail"]["coverage_diagnostics"]
    later = deepcopy(diagnostics[0])
    later["attempt_index"] = 1
    later["report"]["requirements"][0]["complete"] = False
    later["report"]["requirements"][0]["coverage"][0]["status"] = "missing"
    later["report"]["metrics"] = {
        "coverage_rejections": 1,
        "additional_model_calls": 2,
        "additional_tool_calls": 1,
    }
    diagnostics.append(later)
    rows, _ = compare(new)
    coverage = rows[0]["structured_coverage"]
    assert coverage["complete_requirement_rate"]["value"] == 0.5
    assert coverage["attribute_status_counts"] == {
        "covered": 1,
        "missing": 1,
        "not_applicable": 0,
    }
    assert coverage["coverage_attempt_count"] == 3
    assert coverage["coverage_guard"]["additional_model_calls"] == 2


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate_attempt",
        "unknown_requirement",
        "wrong_paper",
        "complete_missing",
        "empty_aspects",
    ],
)
def test_invalid_coverage_binding_or_completeness_is_rejected(corruption):
    new = execution()
    diagnostics = new["detail"]["coverage_diagnostics"]
    requirement = diagnostics[0]["report"]["requirements"][0]
    if corruption == "duplicate_attempt":
        diagnostics.append(deepcopy(diagnostics[0]))
    elif corruption == "unknown_requirement":
        requirement["requirement_id"] = "foreign"
    elif corruption == "wrong_paper":
        diagnostics[0]["report"]["paper_id"] = "foreign"
    elif corruption == "complete_missing":
        requirement["coverage"][0]["status"] = "missing"
    else:
        requirement["coverage"] = []
    with pytest.raises(ValueError):
        compare(new)


def test_stage_aspects_are_counted_separately_and_not_applicable_has_no_fake_recall():
    new = execution()
    requirement = new["detail"]["coverage_diagnostics"][0]["report"]["requirements"][0]
    requirement["coverage"][0]["status"] = "not_applicable"
    requirement["stages"] = [
        {
            "stage_id": "stage1",
            "aspects": [{"aspect_id": "updated_modules", "status": "covered"}],
        },
        {
            "stage_id": "stage2",
            "aspects": [{"aspect_id": "updated_modules", "status": "missing"}],
        },
    ]
    requirement["complete"] = False
    rows, _ = compare(new)
    coverage = rows[0]["structured_coverage"]
    assert coverage["attribute_status_counts"] == {
        "covered": 2,
        "missing": 1,
        "not_applicable": 1,
    }
    assert coverage["attribute_coverage_rate"]["value"] == 2 / 3


@pytest.mark.parametrize("bad", [True, -1, 3, 0.5])
def test_six_dimension_score_range_and_type_are_checked(bad):
    scored = assessment()
    scored["six_dimension_scores"]["factual_accuracy"] = bad
    with pytest.raises(ValueError):
        compare(scored=scored)


def test_semantic_completeness_cannot_exceed_assessed_requirement_denominator():
    with pytest.raises(ValueError, match="semantic_completeness_denominator"):
        compare(scored=assessment(semantic_complete_requirements=3))


def test_true_but_off_scope_numbers_are_not_automatically_counted_as_hallucinations():
    scored = assessment()
    scored["six_dimension_scores"]["scope_compliance"] = 1
    scored["comparison_rubric"]["scope_completeness"] = 1
    _, summary = compare(scored=scored)
    assert (
        summary["treatment_semantics"]["semantic_counts"][
            "unsupported_additional_assertions"
        ]["total"]
        == 0
    )


def test_sensitive_cases_preserve_labels_and_require_explicit_thresholds():
    scored = assessment(
        sensitivity={
            "threshold_description": "Strict requires no unsupported extras; core assesses documented comparison facts separately.",
            "baseline_strict_passed": False,
            "baseline_core_scope_passed": True,
            "treatment_strict_passed": True,
            "treatment_core_scope_passed": True,
        }
    )
    rows, _ = compare(scored=scored)
    assert rows[0]["original_f2_semantic_correct"] is False
    assert rows[0]["sensitivity"]["baseline_core_scope_passed"] is True
    scored["sensitivity"].pop("threshold_description")
    with pytest.raises(ValueError, match="sensitivity_threshold"):
        compare(scored=scored)


def test_change_class_requires_independent_reason_not_a_pass_count_heuristic():
    scored = assessment(
        change_class="unchanged", change_reason="Scope improved but key gaps remain."
    )
    _, summary = compare(scored=scored)
    assert summary["treatment_quality"]["value"] == 1
    assert summary["change_counts"]["unchanged"] == 1
    scored.pop("change_reason")
    with pytest.raises(ValueError, match="missing_change_assessment"):
        compare(scored=scored)


def test_nullable_or_absent_scores_remain_unassessed_and_do_not_fabricate_zero():
    rows, summary = build_comparison([execution()], [execution()], [])
    assert rows[0]["treatment_semantics"] is None
    assert (
        summary["treatment_semantics"]["six_dimension_scores"]["factual_accuracy"][
            "mean"
        ]
        is None
    )
    assert summary["treatment_quality"]["denominator"] == 0
    assert summary["original_f2_quality"]["denominator"] == 0


def test_build_comparison_is_deterministic_and_does_not_mutate_inputs():
    old, new, scored = execution(), execution(), assessment()
    before = deepcopy((old, new, scored))
    first = build_comparison([old], [new], [scored])
    second = build_comparison([old], [new], [scored])
    assert first == second
    assert (old, new, scored) == before


def test_effective_answer_and_reasonable_abstention_are_independent_ai_labels():
    scored = assessment(
        semantic_correct=False,
        effective_answer=True,
        abstention_reasonable=None,
        baseline_semantics={
            "semantic_correct": False,
            "effective_answer": False,
            "abstention_reasonable": True,
        },
    )
    rows, summary = compare(scored=scored)
    assert rows[0]["treatment_semantics"]["effective_answer"] is True
    assert summary["treatment_semantics"]["effective_answer_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1,
    }
    assert summary["baseline_semantics"]["effective_answer_rate"]["value"] == 0
    assert summary["baseline_semantics"]["reasonable_abstention_rate"]["value"] == 1
    assert summary["treatment_semantics"]["reasonable_abstention_rate"]["value"] is None
    assert summary["treatment_quality"]["value"] == 0


def test_missing_independent_answer_or_abstention_labels_have_zero_denominators():
    _, summary = compare()
    assert summary["treatment_semantics"]["effective_answer_rate"]["denominator"] == 0
    assert (
        summary["treatment_semantics"]["reasonable_abstention_rate"]["denominator"] == 0
    )


@pytest.mark.parametrize("field", ["effective_answer", "abstention_reasonable"])
@pytest.mark.parametrize("value", [0, 1, "true", "false"])
def test_answer_and_abstention_labels_require_real_boolean_or_null(field, value):
    with pytest.raises(ValueError, match=f"invalid_boolean_{field}"):
        compare(scored=assessment(**{field: value}))


def test_no_abstention_rate_is_inferred_from_requirement_status():
    new = execution()
    new["detail"]["initial_worker_results"][0]["requirements"][0]["status"] = (
        "insufficient_evidence"
    )
    _, summary = compare(new)
    assert summary["operational_counts"]["new_insufficient_requirements"] == 1
    assert summary["treatment_semantics"]["reasonable_abstention_rate"]["value"] is None


def test_abstention_reasonableness_false_is_counted_without_being_excluded():
    _, summary = compare(scored=assessment(abstention_reasonable=False))
    assert summary["treatment_semantics"]["reasonable_abstention_rate"] == {
        "numerator": 0,
        "denominator": 1,
        "value": 0,
    }


def protocol_failure_execution(*, merged_keeps_error=True):
    """与正式第三题一致的 dependency_failure 包装及永久 JSON 解析错误。"""
    new = execution()
    new["infra_failure"] = False
    worker = new["detail"]["initial_worker_results"][0]
    worker["status"] = "failed"
    worker["requirements"][0]["status"] = "execution_failed"
    worker["error"] = {
        "code": "dependency_failure",
        "message": "Worker dependency call failed after its permitted attempts.",
        "requirement_ids": ["req-paper-a"],
        "details": {
            "phase": "async_dependency",
            "error_type": "JSONDecodeError",
            "message": "Provider tool arguments are not valid JSON.",
            "classification": "permanent",
            "retry_exhausted": False,
            "attempts": 1,
            "retry_count": 0,
        },
    }
    if not merged_keeps_error:
        merged = deepcopy(new["detail"]["outcome"]["merged"]["worker_results"])
        merged[0]["error"] = None
        new["detail"]["outcome"]["merged"]["worker_results"] = merged
    return new


@pytest.mark.parametrize("merged_keeps_error", [True, False])
def test_real_json_dependency_failure_is_scored_as_protocol_not_connection_failure(
    merged_keeps_error,
):
    new = protocol_failure_execution(merged_keeps_error=merged_keeps_error)
    rows, summary = compare(
        new, assessment(semantic_correct=False, change_class="degraded")
    )
    result = rows[0]["treatment_execution"]
    assert result["infra_failure"] is False
    assert result["output_protocol_failure"] is True
    assert result["unknown_dependency_failure"] is False
    assert result["infra_classification_source"] == "explicit_execution_flag"
    assert summary["treatment_quality"] == {
        "numerator": 0,
        "denominator": 1,
        "value": 0,
    }
    assert summary["change_counts"]["degraded"] == 1
    assert summary["operational_counts"]["output_protocol_failure_cases"] == 1


def test_generic_dependency_without_error_type_does_not_claim_infrastructure_failure():
    new = execution()
    worker = new["detail"]["initial_worker_results"][0]
    worker["error"] = {"code": "dependency_failure"}
    worker["requirements"][0]["status"] = "execution_failed"
    rows, summary = compare(new)
    assert rows[0]["treatment_execution"]["infra_failure"] is False
    assert rows[0]["treatment_execution"]["unknown_dependency_failure"] is True
    assert summary["operational_counts"]["unknown_dependency_failure_cases"] == 1


@pytest.mark.parametrize("explicit", [True, False])
def test_explicit_execution_infrastructure_flag_is_authoritative(explicit):
    new = execution()
    new["infra_failure"] = explicit
    worker = new["detail"]["initial_worker_results"][0]
    worker["error"] = {
        "code": "dependency_failure",
        "details": {"error_type": "APIConnectionError"},
    }
    rows, _ = compare(new)
    assert rows[0]["treatment_execution"]["infra_failure"] is explicit


def test_public_parsing_phase_without_error_type_is_still_protocol_failure():
    new = execution()
    worker = new["detail"]["initial_worker_results"][0]
    worker["error"] = {
        "code": "dependency_failure",
        "details": {"phase": "llm_response_parsing"},
    }
    rows, _ = compare(new)
    assert rows[0]["treatment_execution"]["output_protocol_failure"] is True
    assert rows[0]["treatment_execution"]["infra_failure"] is False


def test_invalid_explicit_infrastructure_flag_is_not_silently_truth_coerced():
    new = execution()
    new["infra_failure"] = "false"
    with pytest.raises(ValueError, match="invalid_boolean_infra_failure"):
        compare(new)
