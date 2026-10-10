"""把已完成研究映射为工作台 DTO，不生成事实、不重新执行审核。"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.evidence.corpus import PaperCorpus
from app.research.run_events import EventSource, safe_details
from app.research.synthesis_models import ResearchReport

_ACTION_FIELDS = {
    "step",
    "tool_name",
    "status",
    "execution_status",
    "summary",
    "issue_code",
    "expected",
    "actual_action_count",
    "recovery_instruction",
    "correction_allowed",
    "protocol_corrections",
    "model_calls",
    "tool_calls",
    "latency_ms",
    "evidence_ids",
    "investigated_requirement_ids",
    "last_new_evidence_count",
    "protocol_recovery",
    "requirement_id",
    "requirement_ids",
    "paper_id",
}
_ARGUMENT_FIELDS = {
    "query",
    "paper_ids",
    "paper_id",
    "top_k",
    "requirement_id",
    "requirement_ids",
    "page",
    "figure_id",
    "figure_evidence_id",
    "context_evidence_ids",
    "question",
    "status",
    "claim",
    "evidence_ids",
    "limitations",
}
_REQUIREMENT_FIELDS = {
    "requirement_id",
    "paper_id",
    "dimension_id",
    "question",
    "status",
    "claim",
    "evidence_ids",
    "required",
}
_REVIEW_FIELDS = {
    "worker_task_id",
    "requirement_id",
    "paper_id",
    "status",
    "reason_code",
    "validation_status",
    "skip_reason",
    "claim",
    "evidence_ids",
}
_TRACE_FIELDS = {
    "worker_task_id",
    "paper_id",
    "requirement_count",
    "resolved_count",
    "status",
    "tool_calls",
    "model_calls",
    "llm_calls",
    "vlm_tool_attempts",
    "protocol_corrections",
    "retry_count",
    "dependency_calls",
    "retried_dependency_calls",
    "latency_ms",
}
_PLANNER_FIELDS = {
    "status",
    "candidate_paper_count",
    "dimension_count",
    "dimension_ids",
    "requirement_count",
    "worker_task_count",
    "model_calls",
    "latency_ms",
    "error_code",
    "events",
}
_RECOVERY_FIELDS = {
    "recovery_eligible_count",
    "recovery_attempted_count",
    "recovery_passed_count",
    "recovery_failed_count",
    "recovery_skipped_count",
    "recovery_conversion_rate",
    "additional_model_calls",
    "additional_tool_calls",
    "recovery_latency_ms",
    "recovery_rounds_used",
    "prior_usage",
    "reserved_budget",
    "research_usage",
}


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list:
    return value if isinstance(value, list) else []


def _pick(value: Any, fields: set[str]) -> dict:
    record = _mapping(value)
    return safe_details({key: record[key] for key in fields if key in record})


def _normalize_execution(execution: dict) -> dict:
    """兼容实时入口 wrapper 与历史紧凑记录，不复制业务执行。"""
    research = _mapping(execution.get("research"))
    if not research:
        return execution
    normalized = {**execution, **research}
    # 规划结果来自外层入口，不能被研究快照中的 plan 替代或抹掉。
    if "preparation" in execution:
        normalized["preparation"] = execution["preparation"]
    if "execution_summary" not in normalized:
        summary = {
            "research_metrics": _mapping(research.get("metrics")),
            "planner_model_calls": execution.get("planner_model_calls", 0),
        }
        if "llm_provider_attempts_observed" in research:
            summary["llm_provider_attempts_observed"] = (
                execution.get("planner_model_calls", 0)
                + research["llm_provider_attempts_observed"]
            )
        if "vlm_client_calls_observed" in research:
            summary["vlm_client_calls_observed"] = research["vlm_client_calls_observed"]
        if "total_latency_ms" in execution:
            summary["total_latency_ms"] = execution["total_latency_ms"]
        normalized["execution_summary"] = summary
    return normalized


def _issue(value: Any) -> dict | None:
    record = _mapping(value)
    if not record:
        return None
    # 不转发任意异常 message/details；公开分类足以定位执行阶段。
    return _pick(record, {"code", "retryable", "requirement_ids", "requirement_id"})


def _action(value: Any) -> dict:
    action = _pick(value, _ACTION_FIELDS)
    record = _mapping(value)
    if isinstance(record.get("arguments"), Mapping):
        action["arguments"] = _pick(record["arguments"], _ARGUMENT_FIELDS)
    failure = _mapping(record.get("failure"))
    if failure:
        action["failure"] = _pick(failure, {"phase", "error_type", "retryable", "hint"})
        action["failure"]["validation_errors"] = [
            _pick(item, {"field", "type"})
            for item in _sequence(failure.get("validation_errors"))
        ]
    return action


def execution_diagnostics(execution: dict) -> dict:
    """只投影公开动作和需求；模型请求/响应不属于节点详情。"""
    execution = _normalize_execution(execution)
    outcome = _mapping(execution.get("outcome"))
    workers = _sequence(execution.get("initial_worker_results"))
    reviews = _sequence(execution.get("initial_review_results"))
    if not workers:
        workers = _sequence(outcome.get("worker_results"))
    if not reviews:
        reviews = _sequence(outcome.get("review_results"))
    worker_dtos = []
    for item in workers:
        record = _mapping(item)
        dto = _pick(
            record,
            {
                "worker_task_id",
                "paper_id",
                "status",
                "attempts",
                "latency_ms",
                "observed_evidence_ids",
            },
        )
        dto["requirements"] = [
            _pick(req, _REQUIREMENT_FIELDS)
            for req in _sequence(record.get("requirements"))
        ]
        trace = _mapping(record.get("trace"))
        dto["trace"] = _pick(trace, _TRACE_FIELDS)
        dto["trace"]["actions"] = [
            _action(action) for action in _sequence(trace.get("actions"))
        ]
        dto["error"] = _issue(record.get("error"))
        worker_dtos.append(dto)
    review_dtos = []
    for item in reviews:
        record = _mapping(item)
        dto = _pick(record, _REVIEW_FIELDS)
        dto["trace"] = _pick(
            record.get("trace"), {"model_calls", "retry_count", "latency_ms"}
        )
        dto["issue"] = _issue(record.get("issue"))
        review_dtos.append(dto)
    preparation = _mapping(execution.get("preparation"))
    plan = _mapping(preparation.get("plan"))
    planner = _pick(preparation.get("trace"), _PLANNER_FIELDS)
    planner["dimensions"] = [
        _pick(item, {"dimension_id", "name", "description", "research_question"})
        for item in _sequence(plan.get("dimensions"))
    ]
    planner["paper_ids"] = safe_details(plan.get("paper_ids", []))
    recovery = _pick(
        execution.get("recovery_metrics", outcome.get("metrics")), _RECOVERY_FIELDS
    )
    eligibility = _sequence(
        execution.get("recovery_eligibility", outcome.get("eligibility"))
    )
    recovery["eligibility"] = [
        _pick(item, {"requirement_id", "paper_id", "eligible", "reason_code"})
        for item in eligibility
    ]
    recovery["tasks"] = [
        _pick(
            item,
            {
                "recovery_task_id",
                "paper_id",
                "target_requirement_ids",
                "attempt_index",
                "max_attempts",
                "remaining_budget",
            },
        )
        for item in _sequence(outcome.get("tasks"))
    ]
    merged = _mapping(outcome.get("merged"))
    # 最终合并状态属于恢复后的公开结果，不能覆盖初始 Worker/Reviewer 历史。
    recovery["final_requirements"] = [
        _pick(requirement, _REQUIREMENT_FIELDS)
        for worker in _sequence(merged.get("worker_results"))
        for requirement in _sequence(_mapping(worker).get("requirements"))
    ]
    recovery["final_reviews"] = [
        _pick(item, _REVIEW_FIELDS) for item in _sequence(merged.get("review_results"))
    ]
    return {
        "planner": planner,
        "workers": worker_dtos,
        "reviews": review_dtos,
        "recovery": recovery,
    }


def _source_report(execution: dict) -> Any:
    execution = _normalize_execution(execution)
    outcome = _mapping(execution.get("outcome"))
    return outcome.get("report", execution.get("report"))


def _public_metrics(execution: dict, report_metrics: dict | None = None) -> dict:
    """报告不存在时也保留实际执行成本，避免取消任务丢失调用账本。"""
    metrics = dict(report_metrics or {})
    summary = _mapping(execution.get("execution_summary"))
    research_metrics = _mapping(summary.get("research_metrics"))
    if "llm_provider_attempts_observed" in summary:
        metrics["model_calls"] = summary["llm_provider_attempts_observed"]
        metrics["model_calls_scope"] = "llm_physical_attempts_including_planner"
    elif "total_model_calls" in research_metrics:
        metrics["model_calls"] = research_metrics["total_model_calls"] + summary.get(
            "planner_model_calls", 0
        )
    if "total_tool_calls" in research_metrics:
        metrics["tool_calls"] = research_metrics["total_tool_calls"]
    if "total_latency_ms" in summary:
        metrics["total_latency_ms"] = summary["total_latency_ms"]
    if "vlm_client_calls_observed" in summary:
        metrics["vlm_calls"] = summary["vlm_client_calls_observed"]
    if "model_calls_observed" in execution:
        # 外层在所有线程/异步依赖 drain 后写入真实预算账本，优先于取消前快照。
        metrics["model_calls"] = execution["model_calls_observed"]
        metrics["model_calls_scope"] = "all_physical_model_attempts_including_vlm"
        if "llm_calls_observed" in execution:
            metrics["llm_calls"] = execution["llm_calls_observed"]
        if "vlm_calls_observed" in execution:
            metrics["vlm_calls"] = execution["vlm_calls_observed"]
        elif "vlm_calls" in metrics:
            metrics["vlm_calls_scope"] = "inner_execution_snapshot"
    return safe_details(metrics)


def present_execution(execution: dict, corpus: PaperCorpus | None = None) -> dict:
    """最终事实仅取自权威 ResearchReport；没有报告就不补写 Worker 论断。"""
    execution = _normalize_execution(execution)
    raw_report = _source_report(execution)
    diagnostics = execution_diagnostics(execution)
    base = {
        "status": "report_unavailable",
        "title": "",
        "question": "",
        "paper_ids": [],
        "markdown": "",
        "verified_claims": [],
        "missing_requirements": [],
        "comparisons": [],
        "limitations": [],
        "metrics": _public_metrics(execution),
        "human_review": "not_performed",
        "diagnostics": diagnostics,
    }
    if "replay_provenance" in execution:
        provenance = _mapping(execution["replay_provenance"])
        base["provenance"] = {
            **_pick(provenance, {"case_id", "note"}),
            "source": "replay",
            "source_files": [
                _pick(item, {"path", "sha256"})
                for item in _sequence(provenance.get("source_files"))
            ],
        }
    if raw_report is None:
        return base
    try:
        report = (
            raw_report
            if isinstance(raw_report, ResearchReport)
            else ResearchReport.model_validate(raw_report)
        )
    except (ValidationError, TypeError, ValueError):
        return {
            **base,
            "status": "report_invalid",
            "issue_code": "invalid_report_structure",
        }

    # 复用已有确定性渲染；只使用已核验 Claim 正文，不调用模型生成展示文案。
    from scripts.smoke_research_synthesis import render_markdown

    try:
        markdown = render_markdown(report)
    except KeyError:
        # 历史文件损坏时只返回展示错误，不替不存在的 Claim 拼写正文。
        return {
            **base,
            "status": "report_invalid",
            "issue_code": "invalid_report_claim_reference",
        }

    corpus_evidence = {}
    if corpus is not None:
        corpus_evidence = {
            item.evidence_id: item
            for item in (*corpus.text_evidence, *corpus.figure_evidence)
        }
    claims = []
    for claim in report.verified_claims:
        item = claim.model_dump(mode="json")
        item["review_status"] = "passed"
        for evidence in item["evidence"]:
            evidence["source"] = "report_snapshot"
            evidence["corpus_check"] = "not_checked"
            if corpus is not None:
                current = corpus_evidence.get(evidence["evidence_id"])
                if current is None:
                    evidence["corpus_check"] = "missing"
                else:
                    matches = all(
                        getattr(current, key, None) == evidence.get(key)
                        for key in ("paper_id", "page", "text", "figure_id", "caption")
                    )
                    evidence["corpus_check"] = "matched" if matches else "mismatch"
        claims.append(item)
    missing = [
        {
            "requirement_id": item.requirement_id,
            "paper_id": item.paper_id,
            "dimension_id": item.dimension_id,
            "status": item.status,
            "reason_code": item.issue.code if item.issue else None,
        }
        for item in report.missing_evidence
    ]
    comparisons = [
        {**comparison.model_dump(mode="json"), "dimension_id": section.dimension_id}
        for section in report.sections
        for comparison in section.comparisons
    ]
    metrics = _public_metrics(execution, report.metrics.model_dump(mode="json"))
    return safe_details(
        {
            **base,
            "status": report.status,
            "title": report.title,
            "question": report.question,
            "paper_ids": report.paper_ids,
            "markdown": markdown,
            "verified_claims": claims,
            "missing_requirements": missing,
            "comparisons": comparisons,
            "limitations": report.limitations,
            "metrics": metrics,
            "generation_degraded": report.generation_degraded,
            "diagnostics": diagnostics,
        }
    )


def execution_events(execution: dict, source: EventSource = "derived") -> list[dict]:
    """从结果派生节点状态，不虚构真实开始时间、并发时间线或中间动作。"""
    execution = _normalize_execution(execution)
    if source not in {"live", "derived", "replay"}:
        raise ValueError("invalid_event_source")
    diagnostics = execution_diagnostics(execution)
    events = []

    def append(node_type, node_id, status, summary, details):
        events.append(
            {
                "timestamp": None,
                "node_type": node_type,
                "node_id": node_id,
                "status": status,
                "event_type": status
                if status in {"completed", "failed", "skipped"}
                else "progress",
                "summary": summary,
                "details": safe_details(details),
                "source": source,
            }
        )

    planner = diagnostics["planner"]
    if planner.get("status"):
        status = "completed" if planner["status"] == "success" else "failed"
        append(
            "Planner",
            "planner",
            status,
            "根据实际规划结果派生状态；未保存开始时间。",
            planner,
        )
    else:
        append(
            "Planner",
            "planner",
            "skipped",
            "记录缺少规划结果，无法还原该节点。",
            {"record_available": False},
        )
    for worker in diagnostics["workers"]:
        paper_id = worker.get("paper_id", "unknown")
        status = (
            "completed"
            if worker.get("status") in {"completed", "partial_success"}
            else "failed"
        )
        append(
            "Worker",
            f"worker:{paper_id}",
            status,
            "已读取原始 Worker 结果；状态不代表全部研究需求已验证。",
            worker,
        )
    for review in diagnostics["reviews"]:
        requirement_id = review.get("requirement_id", "unknown")
        validation = review.get("validation_status")
        validator_status = {
            "passed": "completed",
            "rejected": "failed",
            "skipped": "skipped",
        }.get(validation, "skipped")
        append(
            "Validator",
            f"validator:{requirement_id}",
            validator_status,
            "由审核记录的确定性引用校验结果派生；没有独立执行时间。",
            _pick(
                review,
                {
                    "requirement_id",
                    "paper_id",
                    "validation_status",
                    "issue",
                    "evidence_ids",
                },
            ),
        )
        if review.get("status") == "passed":
            status = "completed"
        elif review.get("status") == "rejected":
            status = "failed"
        elif validation == "skipped" or review.get("skip_reason"):
            status = "skipped"
        else:
            status = "failed"
        append(
            "Reviewer",
            f"reviewer:{requirement_id}",
            status,
            "显示原审核结论；未审核与审核拒绝保持区别。",
            review,
        )
    recovery = diagnostics["recovery"]
    attempted = recovery.get("recovery_attempted_count")
    if attempted is None:
        append(
            "Recovery",
            "recovery",
            "skipped",
            "记录没有恢复阶段结果，未推断是否执行。",
            {"record_available": False},
        )
    elif attempted == 0:
        append(
            "Recovery", "recovery", "skipped", "原记录显示没有尝试定向恢复。", recovery
        )
    else:
        status = "failed" if recovery.get("recovery_failed_count", 0) else "completed"
        append("Recovery", "recovery", status, "根据原记录展示有界恢复结果。", recovery)
    raw_report = _mapping(_source_report(execution))
    if raw_report:
        report_status = raw_report.get("status")
        status = (
            "completed"
            if report_status in {"completed", "insufficient_evidence"}
            else "failed"
        )
        append(
            "Synthesis",
            "synthesis",
            status,
            "最终报告状态与研究证据覆盖率分别展示。",
            _pick(raw_report, {"status", "generation_degraded", "metrics", "issue"}),
        )
    else:
        append(
            "Synthesis",
            "synthesis",
            "skipped",
            "记录没有最终报告，不生成研究结论。",
            {"record_available": False},
        )
    return events
