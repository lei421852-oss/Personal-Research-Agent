"""展示最终权威报告与实际节点事实，拒绝以 Worker 草稿补写结论。"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.research.run_events import RunEvent
from app.research.run_presenter import (
    execution_diagnostics,
    execution_events,
    present_execution,
)
from app.research.run_replay import ReplayRepository

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case_id,verified,missing", [("g2", 5, 7), ("g2-1", 4, 10)])
def test_presenter_preserves_actual_claims_and_missing_requirements(
    case_id, verified, missing
):
    execution = ReplayRepository(ROOT).load(case_id)
    original = copy.deepcopy(execution)
    result = present_execution(execution)
    authority = execution["outcome"]["report"]
    assert len(result["verified_claims"]) == verified
    assert len(result["missing_requirements"]) == missing
    assert result["comparisons"] == []
    assert result["status"] == authority["status"] == "completed"
    assert result["human_review"] == "not_performed"
    assert [item["text"] for item in result["verified_claims"]] == [
        item["text"] for item in authority["verified_claims"]
    ]
    assert all(
        evidence["source"] == "report_snapshot"
        and evidence["corpus_check"] == "not_checked"
        for claim in result["verified_claims"]
        for evidence in claim["evidence"]
    )
    assert execution == original
    assert result["metrics"]["tool_calls"] == (9 if case_id == "g2" else 8)
    assert result["metrics"]["model_calls"] == (31 if case_id == "g2" else 19)


def test_snapshot_corpus_check_does_not_replace_report_evidence():
    execution = ReplayRepository(ROOT).load("g2")
    original_evidence = execution["outcome"]["report"]["verified_claims"][0][
        "evidence"
    ][0]
    evidence = SimpleNamespace(**original_evidence)
    corpus = SimpleNamespace(text_evidence=(evidence,), figure_evidence=())
    dto = present_execution(execution, corpus)
    first = dto["verified_claims"][0]["evidence"][0]
    assert first["corpus_check"] == "matched"
    assert first["text"] == original_evidence["text"]
    evidence.text = "different current evidence"
    dto = present_execution(execution, corpus)
    assert dto["verified_claims"][0]["evidence"][0]["corpus_check"] == "mismatch"
    assert dto["verified_claims"][0]["evidence"][0]["text"] == original_evidence["text"]


def test_live_wrapper_uses_actual_research_report_and_separate_planner_calls():
    historical = ReplayRepository(ROOT).load("g2-1")
    wrapper = {
        "preparation": historical["preparation"],
        "planner_model_calls": 1,
        "total_latency_ms": 100,
        "research": {
            "initial_worker_results": historical["initial_worker_results"],
            "initial_review_results": historical["initial_review_results"],
            "outcome": {
                "report": historical["outcome"]["report"],
                "metrics": historical["recovery_metrics"],
            },
            "metrics": {"total_model_calls": 18, "total_tool_calls": 8},
            "llm_provider_attempts_observed": 18,
            "vlm_client_calls_observed": 0,
            "provider_events": [{"reasoning": "HIDDEN"}],
        },
    }
    dto = present_execution(wrapper)
    assert len(dto["verified_claims"]) == 4
    assert len(dto["diagnostics"]["workers"]) == 2
    assert dto["metrics"]["model_calls"] == 19
    assert dto["metrics"]["tool_calls"] == 8
    assert dto["metrics"]["vlm_calls"] == 0
    assert dto["metrics"]["total_latency_ms"] == 100
    assert "HIDDEN" not in json.dumps(dto)
    assert {event["node_type"] for event in execution_events(wrapper)} == {
        "Planner",
        "Worker",
        "Validator",
        "Reviewer",
        "Recovery",
        "Synthesis",
    }


def test_final_outer_model_ledger_overrides_stale_inner_cancel_snapshot():
    historical = ReplayRepository(ROOT).load("g2-1")
    wrapper = {
        "model_calls_observed": 20,
        "llm_calls_observed": 18,
        "vlm_calls_observed": 2,
        "planner_model_calls": 1,
        "research": {
            "outcome": historical["outcome"],
            "metrics": {"total_model_calls": 10, "total_tool_calls": 8},
            "llm_provider_attempts_observed": 10,
            "vlm_client_calls_observed": 0,
        },
    }
    result = present_execution(wrapper)
    assert result["metrics"]["model_calls"] == 20
    assert result["metrics"]["llm_calls"] == 18
    assert result["metrics"]["vlm_calls"] == 2
    assert (
        result["metrics"]["model_calls_scope"]
        == "all_physical_model_attempts_including_vlm"
    )


def test_cancel_before_report_preserves_final_call_metrics_without_facts():
    result = present_execution(
        {
            "planner_model_calls": 1,
            "model_calls_observed": 3,
            "llm_calls_observed": 3,
            "vlm_calls_observed": 0,
            "total_latency_ms": 99,
            "research": {"status": "cancelled"},
        }
    )
    assert result["status"] == "report_unavailable"
    assert result["verified_claims"] == []
    assert result["metrics"]["model_calls"] == 3
    assert result["metrics"]["llm_calls"] == 3
    assert result["metrics"]["vlm_calls"] == 0
    assert result["metrics"]["total_latency_ms"] == 99


def test_no_report_does_not_turn_worker_claims_into_verified_facts():
    execution = {
        "initial_worker_results": [
            {
                "paper_id": "paper",
                "status": "completed",
                "requirements": [{"claim": "UNREVIEWED FACT", "status": "supported"}],
            }
        ]
    }
    result = present_execution(execution)
    assert result["status"] == "report_unavailable"
    assert result["verified_claims"] == []
    assert result["markdown"] == ""
    assert (
        result["diagnostics"]["workers"][0]["requirements"][0]["claim"]
        == "UNREVIEWED FACT"
    )


def test_invalid_report_fails_closed_without_exception_text():
    result = present_execution(
        {"outcome": {"report": {"verified_claims": [{"text": "invented"}]}}}
    )
    assert result["status"] == "report_invalid"
    assert result["verified_claims"] == []
    assert "invented" not in result["markdown"]
    assert result["issue_code"] == "invalid_report_structure"


def test_corrupt_history_with_unknown_claim_reference_is_not_assembled():
    execution = ReplayRepository(ROOT).load("g2")
    execution["outcome"]["report"]["sections"][0]["cells"][0]["claim_id"] = (
        "nonexistent-claim"
    )
    result = present_execution(execution)
    assert result["status"] == "report_invalid"
    assert result["issue_code"] == "invalid_report_claim_reference"
    assert result["markdown"] == ""
    assert result["verified_claims"] == []


def test_empty_record_emits_unknown_stage_as_skipped_without_started_timestamps():
    events = execution_events({})
    assert {event["node_type"] for event in events} == {
        "Planner",
        "Recovery",
        "Synthesis",
    }
    assert all(event["status"] == "skipped" for event in events)
    assert all(event["timestamp"] is None for event in events)
    assert all(event["event_type"] != "started" for event in events)


def test_replay_events_use_all_six_nodes_and_actual_failed_worker():
    events = execution_events(ReplayRepository(ROOT).load("g2"), source="replay")
    assert {event["node_type"] for event in events} == {
        "Planner",
        "Worker",
        "Validator",
        "Reviewer",
        "Recovery",
        "Synthesis",
    }
    assert any(
        event["node_id"] == "worker:flamingo-2204.14198v1"
        and event["status"] == "failed"
        for event in events
    )
    assert any(
        event["node_type"] == "Recovery" and event["status"] == "skipped"
        for event in events
    )
    for identity, event in enumerate(events, 1):
        validated = RunEvent(run_id="run-replay", event_id=identity, **event)
        assert validated.source == "replay"
        assert validated.timestamp is None


@pytest.mark.parametrize("case_id,insufficient", [("g2", 1), ("g2-1", 10)])
def test_legitimate_insufficient_review_is_skipped_not_execution_failed(
    case_id, insufficient
):
    events = execution_events(ReplayRepository(ROOT).load(case_id), source="replay")
    unavailable = [
        event
        for event in events
        if event["node_type"] == "Reviewer"
        and event["details"].get("skip_reason") == "insufficient_evidence"
    ]
    assert len(unavailable) == insufficient
    assert all(event["status"] == "skipped" for event in unavailable)


def test_reviewer_provider_failure_remains_failed_not_evidence_insufficiency():
    events = execution_events(
        {
            "initial_review_results": [
                {
                    "requirement_id": "req",
                    "paper_id": "paper",
                    "status": "not_reviewable",
                    "validation_status": "passed",
                    "skip_reason": None,
                    "issue": {"code": "dependency_failure"},
                }
            ]
        }
    )
    reviewer = next(event for event in events if event["node_type"] == "Reviewer")
    assert reviewer["status"] == "failed"
    assert reviewer["details"]["issue"]["code"] == "dependency_failure"


def test_node_diagnostics_drop_untrusted_provider_output_and_internal_fields():
    execution = {
        "initial_worker_results": [
            {
                "paper_id": "paper",
                "status": "failed",
                "provider_response": {"reasoning": "HIDDEN"},
                "trace": {
                    "actions": [
                        {
                            "tool_name": "search_text",
                            "status": "error",
                            "reasoning": "HIDDEN",
                            "arguments": {
                                "query": "test",
                                "raw_prompt": "HIDDEN",
                                "arbitrary_path": "/private/data",
                            },
                            "failure": {
                                "phase": "worker_action",
                                "error_type": "ValidationError",
                                "message": "HIDDEN",
                                "traceback": "HIDDEN",
                            },
                        }
                    ]
                },
                "error": {
                    "code": "protocol_failure",
                    "message": "HIDDEN",
                    "details": {"password": "HIDDEN"},
                },
            }
        ]
    }
    result = execution_diagnostics(execution)
    serialized = json.dumps(result)
    assert "HIDDEN" not in serialized
    assert "raw_prompt" not in serialized
    assert "arbitrary_path" not in serialized
    assert (
        result["workers"][0]["trace"]["actions"][0]["failure"]["error_type"]
        == "ValidationError"
    )
    assert result["workers"][0]["error"]["code"] == "protocol_failure"
