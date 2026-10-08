"""只准备 Worker 覆盖对照输入和离线指标，不创建 Provider 或检索运行时。"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evidence.models import NonEmpty
from app.research.models import DimensionID, ResearchDimension, ResearchPlan
from app.research.requirements import expand_requirements, group_worker_tasks
from app.research.runtime import ResearchRuntimeConfig

SYSTEM = "coverage_worker_f3_1"
WORKER_FACTORY = "app.research.coverage_worker.CoverageAwarePaperWorker"
RUNTIME_INPUT_FIELDS = ("case_id", "question", "paper_ids", "dimension")


class CoverageComparisonRequest(BaseModel):
    """模型运行输入白名单；评测答案、旧论断和评分备注不能进入此模型。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    case_id: NonEmpty
    question: NonEmpty
    paper_ids: list[NonEmpty] = Field(min_length=2)
    dimension: DimensionID

    @model_validator(mode="after")
    def unique_papers(self):
        if len(set(self.paper_ids)) != len(self.paper_ids):
            raise ValueError("duplicate_paper_id")
        return self


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_hash(record):
    text = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(path):
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [row["case_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_execution_case_id")
    return rows


def build_comparison_plan(request: CoverageComparisonRequest):
    """保留原始需求身份；本地问题投影由新 Worker 的上下文层完成。"""
    return ResearchPlan(
        question=request.question,
        paper_ids=request.paper_ids,
        dimensions=[
            ResearchDimension(
                dimension_id=request.dimension,
                name=request.dimension.replace("_", " "),
                description="Evidence-grounded quality evaluation of the user's question.",
                research_question=(
                    "For the assigned paper only, provide its documented facts needed to address: "
                    + request.question
                ),
            )
        ],
    )


def comparison_worker_tasks(request: CoverageComparisonRequest):
    """仅构造原接口的任务，不实例化 Worker，也不执行模型或工具。"""
    return group_worker_tasks(expand_requirements(build_comparison_plan(request)))


def prepare_comparison(
    executions: Path,
    findings: Path,
    output: Path,
    *,
    diagnostic_executions: Path | None = None,
):
    """从已诊断样本选择同题对照；先验证所有输入，再创建新输出目录。"""
    executions, findings, output = Path(executions), Path(findings), Path(output)
    if output.exists():
        raise ValueError("output_exists_use_new_directory")
    diagnosis = json.loads(findings.read_text(encoding="utf-8"))
    selected_ids = [case["case_id"] for case in diagnosis["cases"]]
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("empty_or_duplicate_diagnostic_case_ids")
    rows = _rows(executions)
    indexed = {row["case_id"]: row for row in rows}
    if not set(selected_ids) <= indexed.keys():
        raise ValueError("diagnostic_case_missing_from_executions")
    diagnostic_source = None
    if diagnostic_executions is not None:
        diagnostic_executions = Path(diagnostic_executions)
        diagnosed = {row["case_id"]: row for row in _rows(diagnostic_executions)}
        if set(diagnosed) != set(selected_ids) or any(
            row != indexed[identity] for identity, row in diagnosed.items()
        ):
            raise ValueError("diagnostic_execution_subset_mismatch")
        diagnostic_source = {
            "file": diagnostic_executions.name,
            "sha256": _sha256(diagnostic_executions),
            "case_count": len(diagnosed),
        }
    requests, source_cases = [], []
    configurations, snapshots = {}, {}
    for case_id in selected_ids:
        row = indexed[case_id]
        if row.get("task_type") != "comparison":
            raise ValueError("selected_case_is_not_comparison")
        projection = row["input_projection"]
        request = CoverageComparisonRequest.model_validate(
            {field: projection[field] for field in RUNTIME_INPUT_FIELDS}
        )
        if request.case_id != case_id:
            raise ValueError("projection_case_identity_mismatch")
        plan = build_comparison_plan(request)
        if plan.model_dump(mode="json") != row["detail"]["plan"]:
            raise ValueError("original_plan_projection_mismatch")
        config = row["detail"]["configuration"]
        ResearchRuntimeConfig.model_validate(config["runtime"])
        configurations[_record_hash(config)] = config
        snapshot = row["detail"]["corpus_snapshot"]
        snapshots[_record_hash(snapshot)] = snapshot
        requests.append(request)
        source_cases.append(
            {
                "case_id": case_id,
                "source_execution_sha256": _record_hash(row),
                "source_input_projection_sha256": _record_hash(projection),
                "runtime_request_sha256": _record_hash(request.model_dump(mode="json")),
                "source_case_sha256": row.get("case_sha256"),
            }
        )
    if len(configurations) != 1 or len(snapshots) != 1:
        raise ValueError("selected_cases_have_different_baselines")
    requests_text = "".join(
        json.dumps(request.model_dump(mode="json"), ensure_ascii=False) + "\n"
        for request in requests
    )
    manifest = {
        "schema_version": 1,
        "status": "prepared_not_executed",
        "system": SYSTEM,
        "worker_class_import": WORKER_FACTORY,
        "case_count": len(requests),
        "case_ids": selected_ids,
        "runtime_input_fields": list(RUNTIME_INPUT_FIELDS),
        "source_execution_file": executions.name,
        "source_execution_file_sha256": _sha256(executions),
        "source_execution_count": len(rows),
        "source_diagnostic_file": findings.name,
        "source_diagnostic_file_sha256": _sha256(findings),
        "diagnostic_executions_source": diagnostic_source,
        "selected_execution_records_sha256": _record_hash(
            [indexed[identity] for identity in selected_ids]
        ),
        "source_cases": source_cases,
        "baseline_configuration": next(iter(configurations.values())),
        "baseline_corpus_snapshot": next(iter(snapshots.values())),
        "requests_file": "requests.jsonl",
        "requests_sha256": hashlib.sha256(requests_text.encode("utf-8")).hexdigest(),
        "provider_calls": 0,
        "retrieval_calls": 0,
        "execution_contract": {
            "worker_factory": "Explicitly construct CoverageAwarePaperWorker with existing adapters, resolver, runtime configuration and matching task.",
            "worker_tasks_builder": "eval.research_quality.coverage_comparison.comparison_worker_tasks",
            "legacy_default_entrypoints_unchanged": True,
            "full_chain_execution_available": False,
            "next_integration": "Provide a new explicit runtime factory for Reviewer/Recovery/Synthesis comparison. Do not replace legacy run_case module globals.",
            "offline_manifest_not_model_input": True,
        },
        "semantic_scoring": {
            "required": "Independent human or explicitly identified AI annotations; never infer semantic quality from coverage declarations or fake execution.",
            "without_annotations": "null metrics with zero assessment denominator",
        },
    }
    output.mkdir(parents=True)
    (output / "requests.jsonl").write_text(requests_text, encoding="utf-8")
    (output / "comparison_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _plain(value: Any):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _ratio(numerator, denominator):
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def summarize_coverage_comparison(
    worker_results: Sequence[Any],
    coverage_requirements: Sequence[Any],
    *,
    semantic_assessments: Sequence[Mapping[str, Any]] = (),
):
    """逐 Case 汇总；完整率必须绑定该 Case 的全部权威需求。

    不把多个独立 Case 的稳定需求 ID 混在一次调用中。若未提供 WorkerResult，
    允许独立统计 sidecar，但结果会明确标记为未绑定 Worker 的声明统计。
    附加断言与最终质量只能汇总独立标注。
    """
    workers = [_plain(worker) for worker in worker_results]
    worker_ids, paper_ids, authoritative_ids = set(), set(), set()
    for worker in workers:
        if not isinstance(worker, Mapping):
            raise TypeError("invalid_authoritative_worker_record")
        worker_id, paper_id = worker.get("worker_task_id"), worker.get("paper_id")
        if (
            not isinstance(worker_id, str)
            or not worker_id.strip()
            or not isinstance(paper_id, str)
            or not paper_id.strip()
        ):
            raise ValueError("invalid_authoritative_worker_identity")
        if worker_id in worker_ids or paper_id in paper_ids:
            raise ValueError("duplicate_authoritative_worker")
        worker_ids.add(worker_id)
        paper_ids.add(paper_id)
        requirements = worker.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError("missing_authoritative_worker_requirements")
        for requirement in requirements:
            identity = requirement.get("requirement_id")
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError("invalid_authoritative_requirement_identity")
            if identity in authoritative_ids:
                raise ValueError("duplicate_authoritative_requirement")
            authoritative_ids.add(identity)
    coverage, sidecar_metrics = [], []
    for value in coverage_requirements:
        value = _plain(value)
        if "requirements" in value:
            coverage.extend(value["requirements"])
            sidecar_metrics.append(value.get("metrics", {}))
        else:
            coverage.append(value)
    requirement_ids = [requirement["requirement_id"] for requirement in coverage]
    if len(set(requirement_ids)) != len(requirement_ids):
        raise ValueError("duplicate_coverage_requirement")
    if workers and set(requirement_ids) != authoritative_ids:
        # 拒绝丢失和无关 sidecar；否则两个需求只送一个也会被误报为 1/1 完整。
        raise ValueError("coverage_requirement_set_mismatch")
    counts = dict.fromkeys(("covered", "missing", "not_applicable"), 0)
    complete = 0
    for requirement in coverage:
        attributes = list(
            requirement.get("coverage", requirement.get("attributes", []))
        )
        identities = [
            (None, attribute.get("aspect_id", attribute.get("attribute_id")))
            for attribute in attributes
        ]
        # 阶段属性独立计数；第一、第二阶段可拥有同名 frozen_modules 属性。
        stage_ids = []
        for stage in requirement.get("stages", []):
            stage_ids.append(stage["stage_id"])
            stage_attributes = stage["aspects"]
            identities.extend(
                (stage["stage_id"], attribute["aspect_id"])
                for attribute in stage_attributes
            )
            attributes.extend(stage_attributes)
        if not attributes or len(set(identities)) != len(identities):
            raise ValueError("empty_or_duplicate_coverage_attributes")
        if any(identity[1] is None for identity in identities) or len(
            set(stage_ids)
        ) != len(stage_ids):
            raise ValueError("invalid_coverage_attribute_identity")
        statuses = [attribute["status"] for attribute in attributes]
        if not set(statuses) <= counts.keys():
            raise ValueError("invalid_coverage_status")
        for status in statuses:
            counts[status] += 1
        if requirement.get("complete") is True and "missing" in statuses:
            raise ValueError("complete_flag_conflicts_with_missing_coverage")
        if "complete" in requirement and not isinstance(requirement["complete"], bool):
            raise ValueError("invalid_coverage_complete_flag")
        complete += requirement.get("complete", "missing" not in statuses)
    extra_costs = {
        "precondition_rejected_supported_attempts": 0,
        "additional_model_calls": 0,
        "additional_tool_calls": 0,
    }
    extra_lookup_observed = False
    for metrics in sidecar_metrics:
        for source, destination in (
            ("coverage_rejections", "precondition_rejected_supported_attempts"),
            ("additional_model_calls", "additional_model_calls"),
            ("additional_tool_calls", "additional_tool_calls"),
        ):
            value = metrics.get(source, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("invalid_coverage_guard_metric")
            extra_costs[destination] += value
        extra_lookup = metrics.get("extra_lookup_observed", False)
        if not isinstance(extra_lookup, bool):
            raise TypeError("invalid_extra_lookup_flag")
        extra_lookup_observed |= extra_lookup
    costs = {"tool_calls": 0, "model_calls": 0, "latency_ms": 0.0}
    cost_denominator = 0
    for worker in workers:
        trace = worker.get("trace")
        if trace is None:
            continue
        if not isinstance(trace, Mapping):
            raise TypeError("invalid_worker_cost_trace")
        for key in costs:
            value = trace.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or (key != "latency_ms" and not isinstance(value, int))
            ):
                raise ValueError("invalid_worker_cost")
            costs[key] += value
        cost_denominator += 1
    semantic_case_ids = set()
    for assessment in semantic_assessments:
        if not isinstance(assessment, Mapping):
            raise TypeError("invalid_semantic_assessment_record")
        identity = assessment.get("case_id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("invalid_semantic_assessment_identity")
        # 同一 Case 的不同评分字段应放在同一条标注中，避免重复累计分母。
        if identity in semantic_case_ids:
            raise ValueError("duplicate_semantic_assessment_case")
        semantic_case_ids.add(identity)
    semantic = {}
    for field in (
        "unsupported_additional_assertions",
        "premature_supported",
        "final_comparison_quality",
    ):
        assessed = []
        for assessment in semantic_assessments:
            if field not in assessment or assessment[field] is None:
                continue
            if (
                assessment.get("source") not in {"human", "ai"}
                or not assessment.get("assessor")
                or not assessment.get("case_id")
            ):
                raise ValueError("missing_semantic_assessment_provenance")
            value = assessment[field]
            if field == "final_comparison_quality":
                if not isinstance(value, bool):
                    raise ValueError("comparison_quality_requires_boolean")
                value = int(value)
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("semantic_error_count_requires_nonnegative_integer")
            assessed.append(value)
        semantic[field] = {
            "value": sum(assessed) / len(assessed)
            if assessed and field == "final_comparison_quality"
            else sum(assessed)
            if assessed
            else None,
            "denominator": len(assessed),
            "annotation_based": True,
        }
    return {
        "coverage_scope": "authoritative_worker_requirements"
        if workers
        else "sidecar_only_without_worker_binding",
        "authoritative_requirement_count": len(authoritative_ids) if workers else None,
        "coverage_semantics": "Policy-declared structural coverage; not semantic correctness or independent verification.",
        "attribute_status_counts": counts,
        "attribute_coverage_rate": _ratio(
            counts["covered"], counts["covered"] + counts["missing"]
        ),
        "complete_requirement_rate": _ratio(complete, len(coverage)),
        "coverage_guard": {
            **extra_costs,
            "extra_lookup_observed": extra_lookup_observed,
            "denominator": len(sidecar_metrics),
            "semantics": "Rejected supported attempts were not committed as supported; extra costs are observed after feedback, not a causal counterfactual cost estimate.",
        },
        "worker_cost": {
            **costs,
            "denominator": cost_denominator,
            "missing_cost_records": len(workers) - cost_denominator,
            "latency_semantics": "Sum of recorded Worker latencies, not concurrent research wall-clock time.",
        },
        "semantic_quality": semantic,
    }
