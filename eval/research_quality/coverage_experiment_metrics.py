"""F3.1-E 离线对照统计；自报覆盖、结构验证和语义评分分别计算。"""

import math
from collections import Counter
from collections.abc import Mapping

SIX_DIMENSIONS = (
    "factual_accuracy",
    "evidence_support",
    "requirement_completeness",
    "comparison_quality",
    "scope_compliance",
    "abstention_reasonableness",
)
F2_RUBRIC_FIELDS = (
    "fact_accuracy",
    "difference_correctness",
    "evidence_grounding",
    "scope_completeness",
)
SEMANTIC_COUNTS = (
    "unsupported_additional_assertions",
    "evidence_boundary_violations",
    "premature_supported",
)
CHANGE_CLASSES = {"improved", "unchanged", "degraded", "not_comparable"}


def _index(rows, kind):
    """拒绝重复身份；不通过取最后一项掩盖重复执行或重复评分。"""
    indexed = {}
    for row in rows:
        identity = row.get("case_id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"invalid_{kind}_case_id")
        if identity in indexed:
            raise ValueError(f"duplicate_{kind}_case_id")
        indexed[identity] = row
    return indexed


def _number(value, *, integer=False):
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (integer and not isinstance(value, int))
    ):
        raise ValueError("invalid_nonnegative_metric")
    return value


def _rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _optional_bool(record, name):
    value = record.get(name)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"invalid_boolean_{name}")
    return value


def _semantics(record):
    """空评分保持缺失；四维采用原 F2 字段，不将新六维冒充历史评分。"""
    record = record or {}
    result = {
        field: _optional_bool(record, field)
        for field in ("semantic_correct", "effective_answer", "abstention_reasonable")
    }
    for field, keys in (
        ("six_dimension_scores", SIX_DIMENSIONS),
        ("comparison_rubric", F2_RUBRIC_FIELDS),
    ):
        values = record.get(field) or {}
        if not isinstance(values, Mapping) or set(values) - set(keys):
            raise ValueError(f"invalid_{field}_fields")
        checked = {}
        for key in keys:
            value = _number(values.get(key), integer=True)
            if value is not None and value > 2:
                raise ValueError("rubric_score_out_of_range")
            checked[key] = value
        result[field] = checked
    for field in SEMANTIC_COUNTS:
        result[field] = _number(record.get(field), integer=True)
    complete = _number(record.get("semantic_complete_requirements"), integer=True)
    assessed = _number(record.get("assessed_requirements"), integer=True)
    if (complete is None) != (assessed is None) or (
        complete is not None and complete > assessed
    ):
        raise ValueError("invalid_semantic_completeness_denominator")
    result["semantic_completeness"] = (
        _rate(complete, assessed) if assessed is not None else None
    )
    return result


def _detail(row):
    return (row or {}).get("detail") or {}


def _final_workers(row):
    detail = _detail(row)
    merged = (detail.get("outcome") or {}).get("merged") or {}
    return merged.get("worker_results", detail.get("initial_worker_results", []))


def _failure_kind(error):
    """依赖包装码不等于连接故障；公开解析错误属于输出协议错误。"""
    if not isinstance(error, Mapping):
        return None
    details = error.get("details") or {}
    code = error.get("code", error.get("kind"))
    error_type = details.get("error_type", error.get("error_type"))
    cause_type = details.get("cause_type", error.get("cause_type"))
    phase = details.get("phase", error.get("phase"))
    if (
        error_type in {"JSONDecodeError", "StructuredOutputError"}
        or phase in {"llm_response_parsing", "tool_arguments_parsing"}
        or code in {"protocol_failure", "multiple_actions", "malformed_output"}
    ):
        return "output_protocol_failure"
    transport_types = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutError",
        "RateLimitError",
        "InternalServerError",
    }
    if error_type in transport_types or cause_type in transport_types:
        return "infrastructure_failure"
    if code in {
        "provider_failure",
        "provider_transient_failure",
        "provider_permanent_failure",
    }:
        return "infrastructure_failure"
    if code == "dependency_failure":
        return "unknown_dependency_failure"
    return None


def _execution(row):
    """最终状态与历史重试分开；恢复后成功的旧失败不再算最终基础设施故障。"""
    detail = _detail(row)
    outcome = detail.get("outcome") or {}
    report = outcome.get("report") or {}
    workers = _final_workers(row)
    merged = outcome.get("merged") or {}
    reviews = merged.get("review_results", detail.get("initial_review_results", []))
    requirements = [r for worker in workers for r in worker.get("requirements", [])]
    explicit_infra = _optional_bool(row or {}, "infra_failure")
    errors = [worker.get("error") for worker in workers]
    errors += [review.get("issue") for review in reviews]
    errors += [report.get("issue")]
    failed_ids = {
        r.get("requirement_id")
        for r in requirements
        if r.get("status") == "execution_failed"
    }
    # 既有合并器可能丢弃 error；只追溯最终仍失败的需求，不把已恢复的旧失败计入。
    for worker in detail.get("initial_worker_results", []):
        if any(
            r.get("requirement_id") in failed_ids
            for r in worker.get("requirements", [])
        ):
            errors.append(worker.get("error"))
    kinds = {_failure_kind(error) for error in errors}
    infra = (
        explicit_infra
        if explicit_infra is not None
        else "infrastructure_failure" in kinds
    )
    statuses = Counter(r.get("status", "unknown") for r in requirements)
    return {
        "worker_statuses": [w.get("status") for w in workers],
        "requirement_status_counts": dict(statuses),
        "requirement_count": len(requirements),
        "reviewer_status_counts": dict(Counter(r.get("status") for r in reviews)),
        "report_status": report.get("status", detail.get("status")),
        "degraded_generation": report.get("generation_degraded"),
        "infra_failure": infra,
        "infra_classification_source": "explicit_execution_flag"
        if explicit_infra is not None
        else "public_error_type",
        "output_protocol_failure": "output_protocol_failure" in kinds,
        "unknown_dependency_failure": "unknown_dependency_failure" in kinds,
        "execution_failed": bool(statuses["execution_failed"]),
        "insufficient_requirement_count": statuses["insufficient_evidence"],
        "missing_prediction": row is None or row.get("prediction") is None,
    }


def _cost(row):
    """优先读实际总成本；缺失不回填零，也不把并发 Worker 耗时求和当墙钟。"""
    detail = _detail(row)
    sources = [detail.get("metrics") or {}, (row or {}).get("metrics") or {}]
    result = {}
    for field, names in (
        ("model_calls", ("total_model_calls", "model_calls")),
        ("tool_calls", ("total_tool_calls", "tool_calls")),
        ("latency_ms", ("wall_time_ms", "latency_ms", "total_latency_ms")),
    ):
        value = next(
            (s[name] for s in sources for name in names if s.get(name) is not None),
            None,
        )
        if value is None and field == "latency_ms":
            value = (row or {}).get("latency_ms")
        result[field] = _number(value, integer=field != "latency_ms")
    result["provider_attempts"] = _number(
        (row or {}).get(
            "provider_attempts", detail.get("llm_provider_attempts_observed")
        ),
        integer=True,
    )
    return result


def _coverage(row):
    """同一 Worker 只用最终 attempt 的覆盖计算完整率，所有 attempt 的成本另计。"""
    detail = _detail(row)
    diagnostics = (row or {}).get("coverage_diagnostics")
    if diagnostics is None:
        diagnostics = detail.get("coverage_diagnostics", [])
    authority = {
        (worker["worker_task_id"], requirement["requirement_id"]): worker["paper_id"]
        for worker in _final_workers(row)
        for requirement in worker.get("requirements", [])
    }
    final, seen = {}, set()
    guard = {
        key: []
        for key in (
            "coverage_rejections",
            "additional_model_calls",
            "additional_tool_calls",
        )
    }
    reports = 0
    for diagnostic in diagnostics:
        worker_id = diagnostic["worker_task_id"]
        attempt = _number(diagnostic.get("attempt_index", 0), integer=True)
        if (worker_id, attempt) in seen:
            raise ValueError("duplicate_coverage_attempt")
        seen.add((worker_id, attempt))
        report = diagnostic.get("report")
        if report is None:
            continue
        reports += 1
        for key, values in guard.items():
            value = _number((report.get("metrics") or {}).get(key), integer=True)
            if value is not None:
                values.append(value)
        if report.get("paper_id", diagnostic.get("paper_id")) != diagnostic.get(
            "paper_id"
        ):
            raise ValueError("coverage_paper_mismatch")
        for requirement in report.get("requirements", []):
            identity = (worker_id, requirement["requirement_id"])
            if (
                identity not in authority
                or authority[identity] != diagnostic["paper_id"]
            ):
                raise ValueError("coverage_requirement_identity_mismatch")
            previous = final.get(identity)
            if previous is None or attempt > previous[0]:
                final[identity] = (attempt, requirement)
            elif attempt == previous[0]:
                raise ValueError("duplicate_coverage_requirement")
    counts = Counter({"covered": 0, "missing": 0, "not_applicable": 0})
    complete = 0
    for _, requirement in final.values():
        attributes = [(None, a) for a in requirement.get("coverage", [])]
        stage_ids = set()
        for stage in requirement.get("stages", []):
            if stage["stage_id"] in stage_ids:
                raise ValueError("duplicate_coverage_stage")
            stage_ids.add(stage["stage_id"])
            attributes.extend((stage["stage_id"], a) for a in stage.get("aspects", []))
        identities = [(stage, a["aspect_id"]) for stage, a in attributes]
        if not identities or len(set(identities)) != len(identities):
            raise ValueError("empty_or_duplicate_coverage_aspects")
        for _, attribute in attributes:
            status = attribute["status"]
            if status not in counts:
                raise ValueError("invalid_coverage_status")
            counts[status] += 1
        declared = _optional_bool(requirement, "complete")
        if declared and any(a["status"] == "missing" for _, a in attributes):
            raise ValueError("complete_coverage_contains_missing")
        complete += declared is True
    return {
        "semantics": "Policy-declared coverage, not independently validated semantic completeness.",
        "attribute_status_counts": dict(counts),
        "attribute_coverage_rate": _rate(
            counts["covered"], counts["covered"] + counts["missing"]
        ),
        "complete_requirement_rate": _rate(complete, len(authority)),
        "authoritative_requirement_count": len(authority),
        "missing_coverage_requirement_count": len(authority) - len(final),
        "coverage_attempt_count": len(seen),
        "coverage_report_count": reports,
        "coverage_guard": {
            key: sum(values) if values else None for key, values in guard.items()
        },
    }


def _sum_metrics(rows, field):
    result = {}
    for key in ("model_calls", "tool_calls", "latency_ms", "provider_attempts"):
        values = [r[field][key] for r in rows if r[field][key] is not None]
        result[key] = {
            "total": sum(values) if values else None,
            "recorded_cases": len(values),
            "missing_cases": len(rows) - len(values),
        }
    return result


def _semantic_summary(rows, field):
    records = [r[field] for r in rows if r[field] is not None]
    scores = {}
    for key in SIX_DIMENSIONS:
        values = [r["six_dimension_scores"][key] for r in records]
        values = [v for v in values if v is not None]
        scores[key] = {
            "mean": sum(values) / len(values) if values else None,
            "assessed_cases": len(values),
        }
    totals = {}
    for key in SEMANTIC_COUNTS:
        values = [r[key] for r in records if r[key] is not None]
        totals[key] = {
            "total": sum(values) if values else None,
            "assessed_cases": len(values),
        }
    completeness = [r["semantic_completeness"] for r in records]
    completeness = [v for v in completeness if v is not None]
    return {
        "six_dimension_scores": scores,
        "semantic_counts": totals,
        "effective_answer_rate": _semantic_boolean_rate(records, "effective_answer"),
        "reasonable_abstention_rate": _semantic_boolean_rate(
            records, "abstention_reasonable"
        ),
        "independent_semantic_completeness": _rate(
            sum(v["numerator"] for v in completeness),
            sum(v["denominator"] for v in completeness),
        ),
        "provenance": "AI-assisted semantic judgments supplied separately; not human Gold.",
    }


def _semantic_boolean_rate(records, field):
    """分母只含独立评分明确标注的布尔值；null 不适用或未评不算负例。"""
    values = [record[field] for record in records if record[field] is not None]
    return _rate(sum(values), len(values))


def _cost_change(old, new):
    result = {}
    for key in old:
        comparable = old[key] is not None and new[key] is not None
        result[key] = {
            "delta": new[key] - old[key] if comparable else None,
            "ratio": new[key] / old[key] if comparable and old[key] else None,
        }
    return result


def _aggregate_cost_change(rows):
    """只比较两侧都有记录的同题成本，避免缺失执行使总成本看似下降。"""
    result = {}
    for key in ("model_calls", "tool_calls", "latency_ms", "provider_attempts"):
        pairs = [
            (r["baseline_cost"][key], r["treatment_cost"][key])
            for r in rows
            if r["baseline_cost"][key] is not None
            and r["treatment_cost"][key] is not None
        ]
        old, new = sum(a for a, _ in pairs), sum(b for _, b in pairs)
        result[key] = {
            "paired_cases": len(pairs),
            "baseline_total": old if pairs else None,
            "treatment_total": new if pairs else None,
            "delta": new - old if pairs else None,
            "ratio": new / old if pairs and old else None,
        }
    return result


def build_comparison(baseline_rows, treatment_rows, assessments):
    """只汇总传入产物，不读取参考答案、不调用模型、不修改历史标签。

    assessment 的顶层评分描述 treatment；baseline_semantics 是本轮独立复核，
    original_f2_semantic_correct 则保留原 F2 标签。改善类别必须有人工/AI 给出的
    change_class 与 change_reason，不能由是否通过自动推导。
    """
    baseline = _index(baseline_rows, "baseline")
    treatment = _index(treatment_rows, "treatment")
    scored = _index(assessments, "assessment")
    if set(treatment) - set(baseline) or set(scored) - set(baseline):
        raise ValueError("unknown_comparison_case")
    rows = []
    for case_id, old in baseline.items():
        new, assessment = treatment.get(case_id), scored.get(case_id)
        old_execution, new_execution = _execution(old), _execution(new)
        old_semantics = new_semantics = None
        change, reason = (
            "not_comparable",
            "Missing execution or independent assessment.",
        )
        original, sensitivity = None, None
        if assessment is not None:
            if (
                assessment.get("method") != "ai_semantic_review"
                or not assessment.get("assessor")
                or not assessment.get("notes")
            ):
                raise ValueError("missing_ai_assessment_provenance")
            original = _optional_bool(assessment, "original_f2_semantic_correct")
            new_semantics = _semantics(assessment)
            if assessment.get("baseline_semantics") is not None:
                old_semantics = _semantics(assessment["baseline_semantics"])
            change = assessment.get("change_class")
            reason = assessment.get("change_reason")
            if (
                change not in CHANGE_CLASSES
                or not isinstance(reason, str)
                or not reason
            ):
                raise ValueError("missing_change_assessment")
            sensitivity = assessment.get("sensitivity")
            if sensitivity is not None:
                if not isinstance(sensitivity, Mapping) or not sensitivity.get(
                    "threshold_description"
                ):
                    raise ValueError("missing_sensitivity_threshold")
                for key in (
                    "baseline_strict_passed",
                    "baseline_core_scope_passed",
                    "treatment_strict_passed",
                    "treatment_core_scope_passed",
                ):
                    _optional_bool(sensitivity, key)
        eligible = new is not None and not new_execution["missing_prediction"]
        eligible &= not new_execution["infra_failure"]
        if not eligible:
            change = "not_comparable"
        rows.append(
            {
                "case_id": case_id,
                "original_f2_semantic_correct": original,
                "baseline_execution": old_execution,
                "treatment_execution": new_execution,
                "baseline_cost": _cost(old),
                "treatment_cost": _cost(new),
                "baseline_semantics": old_semantics,
                "treatment_semantics": new_semantics,
                "semantic_scoring_eligible": eligible,
                "structured_coverage": _coverage(new),
                "change_class": change,
                "change_reason": reason,
                "sensitivity": sensitivity,
            }
        )
        rows[-1]["cost_change"] = _cost_change(
            rows[-1]["baseline_cost"], rows[-1]["treatment_cost"]
        )
    classes = Counter(r["change_class"] for r in rows)
    originals = [r["original_f2_semantic_correct"] for r in rows]
    originals = [v for v in originals if v is not None]
    eligible_rows = [r for r in rows if r["semantic_scoring_eligible"]]
    qualities = [
        r["treatment_semantics"]["semantic_correct"]
        for r in eligible_rows
        if r["treatment_semantics"] is not None
        and r["treatment_semantics"]["semantic_correct"] is not None
    ]
    coverage = [r["structured_coverage"] for r in rows]
    summary = {
        "case_count": len(rows),
        "treatment_execution_count": len(treatment),
        "assessment_count": len(scored),
        "change_counts": {key: classes[key] for key in sorted(CHANGE_CLASSES)},
        "original_f2_quality": _rate(sum(originals), len(originals)),
        "treatment_quality": _rate(sum(qualities), len(qualities)),
        "baseline_semantics": _semantic_summary(rows, "baseline_semantics"),
        "treatment_semantics": _semantic_summary(eligible_rows, "treatment_semantics"),
        "structured_complete_requirements": _rate(
            sum(c["complete_requirement_rate"]["numerator"] for c in coverage),
            sum(c["complete_requirement_rate"]["denominator"] for c in coverage),
        ),
        "missing_coverage_requirement_count": sum(
            c["missing_coverage_requirement_count"] for c in coverage
        ),
        "operational_counts": {
            "missing_treatment_executions": len(baseline) - len(treatment),
            "missing_predictions": sum(
                r["treatment_execution"]["missing_prediction"] for r in rows
            ),
            "infrastructure_failure_cases": sum(
                r["treatment_execution"]["infra_failure"] for r in rows
            ),
            "output_protocol_failure_cases": sum(
                r["treatment_execution"]["output_protocol_failure"] for r in rows
            ),
            "unknown_dependency_failure_cases": sum(
                r["treatment_execution"]["unknown_dependency_failure"] for r in rows
            ),
            "execution_failed_cases": sum(
                r["treatment_execution"]["execution_failed"] for r in rows
            ),
            "new_insufficient_requirements": sum(
                max(
                    0,
                    r["treatment_execution"]["insufficient_requirement_count"]
                    - r["baseline_execution"]["insufficient_requirement_count"],
                )
                for r in rows
            ),
        },
        "baseline_cost": _sum_metrics(rows, "baseline_cost"),
        "treatment_cost": _sum_metrics(rows, "treatment_cost"),
        "cost_change": _aggregate_cost_change(rows),
        "scope": "Seven known failure cases are diagnostic data, not a generalization benchmark.",
        "quality_semantics": "covered, Reviewer passed and insufficient_evidence do not by themselves imply semantic success; execution_failed is not reasonable abstention.",
    }
    return rows, summary
