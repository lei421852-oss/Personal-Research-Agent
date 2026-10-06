"""Explicit allowlisted projection; never expose messages, prompts or local assets."""

import json

from app.agent.loop import AgentResult
from app.api.schemas.agent import (
    AgentRunResponse,
    AnalysisDTO,
    CitationDTO,
    FigureDTO,
    MetricsDTO,
    TraceStepDTO,
)
from app.evidence.corpus import PaperCorpus
from app.tools.redaction import public_value

FAILURE_FIELDS = {
    "phase",
    "error_type",
    "cause_type",
    "message",
    "hint",
    "http_status",
    "provider_code",
    "issue_code",
    "retryable",
    "validation_errors",
    "reason",
    "expected_tool_calls",
    "actual_tool_calls",
    "requested_tools",
    "tool_calls_used",
    "max_tool_calls",
    "model_calls_used",
}


def public_failure(value):
    if not isinstance(value, dict):
        return None
    return public_value({k: v for k, v in value.items() if k in FAILURE_FIELDS})


def map_agent_result(
    result: AgentResult, corpus: PaperCorpus, *, vlm_calls: int, retrieval_mode: str
) -> AgentRunResponse:
    observations = {}
    for message in result.state.messages:
        if message.get("role") == "tool":
            observations[message["tool_call_id"]] = json.loads(message["content"])
    traces = {t.tool_call_id: t for t in result.state.trace}
    ordered = result.state.model_actions or [
        {
            "tool_call_id": t.tool_call_id,
            "tool_name": t.tool_name,
            "arguments": t.arguments,
        }
        for t in result.state.trace
    ]
    steps = []
    seen_figures = {c.evidence_id for c in result.citations if c.figure_id}
    analyses = {}
    for action in ordered:
        identity = action["tool_call_id"]
        trace, observation = traces.get(identity), observations.get(identity)
        # A budget-rejected request is not an executed step. Protocol failures stay in stop_details.
        if trace is None and (action["tool_name"] != "finish" or observation is None):
            continue
        payload = observation.get("payload", {}) if observation else {}
        records = [*payload.get("evidence", []), *payload.get("figures", [])]
        if payload.get("figure"):
            records.append(payload["figure"])
            seen_figures.add(payload["figure"]["evidence_id"])
            if payload.get("analysis"):
                analyses[payload["figure"]["evidence_id"]] = payload["analysis"]
        for figure in payload.get("figures", []):
            seen_figures.add(figure["evidence_id"])
        ids = list(
            dict.fromkeys(
                e["evidence_id"]
                for e in records
                if isinstance(e, dict) and "evidence_id" in e
            )
        )
        if action["tool_name"] == "finish" and observation["status"] == "success":
            ids = [c["evidence_id"] for c in payload.get("citations", [])]
        # The final answer is shown separately; only finish citation IDs/limitations are needed here.
        arguments = (
            action["arguments"]
            if action["tool_name"] != "finish"
            else {
                k: v
                for k, v in action["arguments"].items()
                if k in {"evidence_ids", "limitations"}
            }
        )
        steps.append(
            TraceStepDTO(
                step=len(steps) + 1,
                tool_name=action["tool_name"],
                arguments=public_value(arguments),
                status=trace.status if trace else observation["status"],
                latency_ms=trace.latency_ms if trace else None,
                evidence_ids=ids,
                failure=public_failure(
                    trace.failure if trace else payload.get("failure")
                ),
            )
        )
    figures = []
    for figure in corpus.figure_evidence:
        if figure.evidence_id not in seen_figures:
            continue
        raw = analyses.get(figure.evidence_id)
        analysis = (
            AnalysisDTO(
                observations=public_value(raw["observations"]),
                interpretation=public_value(raw["interpretation"]),
                evidence_used=raw["evidence_used"],
                limitations=public_value(raw["limitations"]),
            )
            if raw
            else None
        )
        figures.append(
            FigureDTO(
                evidence_id=figure.evidence_id,
                paper_id=figure.paper_id,
                page=figure.page,
                figure_id=figure.figure_id,
                caption=public_value(figure.caption),
                image_url=f"/api/evidence/{figure.evidence_id}/image",
                analysis=analysis,
            )
        )
    return AgentRunResponse(
        status=result.status,
        answer=public_value(result.answer),
        citations=[
            CitationDTO(
                evidence_id=c.evidence_id,
                paper_id=c.paper_id,
                page=c.page,
                figure_id=c.figure_id,
            )
            for c in result.citations
        ],
        limitations=public_value(result.limitations),
        trace=steps,
        metrics=MetricsDTO(
            tool_calls=result.tool_calls,
            model_calls=result.model_calls,
            vlm_calls=vlm_calls,
            total_latency_ms=result.total_latency_ms,
        ),
        figures=figures,
        stop_details=public_failure(result.stop_details) or {},
        retrieval_mode=retrieval_mode,
    )
