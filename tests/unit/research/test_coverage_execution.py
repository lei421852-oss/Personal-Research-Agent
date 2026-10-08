"""独立实验工厂与全链路接线测试；不代表真实语义质量。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.evidence.corpus import PaperCorpus
from app.evidence.models import RetrievedEvidence, TextEvidence
from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.research.coverage_models import ASPECTS_BY_DIMENSION
from app.research.synthesis_models import SynthesisInput
from eval.research_quality.coverage_comparison import CoverageComparisonRequest
from eval.research_quality.coverage_execution import (
    RecordingToolExecutor,
    RecordingVisionClient,
    _provider_events,
    run_coverage_case,
)


def action(name, **arguments):
    return LLMResponse(
        content="private hidden chain of thought",
        tool_calls=[ToolCall(id="synthetic-action", name=name, arguments=arguments)],
    )


def context(messages):
    return json.loads(messages[-1]["content"])


def report(messages, tools):
    source = SynthesisInput.model_validate(context(messages)["input"])
    claims = {c.claim_id: c for c in source.verified_claims}
    return action(
        "submit_report",
        title="Evidence-grounded research report",
        sections=[
            {
                "dimension_id": dim.dimension_id,
                "title": "Verified findings",
                "cells": [
                    cell.model_dump(mode="json")
                    | {
                        "evidence_ids": claims[cell.claim_id].evidence_ids
                        if cell.claim_id
                        else []
                    }
                    for cell in source.cells
                    if cell.dimension_id == dim.dimension_id
                ],
                "comparisons": [],
            }
            for dim in source.plan.dimensions
        ],
    )


@pytest.fixture
def fixture():
    texts = tuple(
        TextEvidence(
            evidence_id="ev-" + paper,
            paper_id=paper,
            page=1,
            text="Synthetic prepared evidence for " + paper,
        )
        for paper in ("paper-a", "paper-b")
    )
    metadata = tuple(
        SimpleNamespace(
            paper_id=e.paper_id, paper_key=e.paper_id, pages=(SimpleNamespace(page=1),)
        )
        for e in texts
    )
    corpus = PaperCorpus(
        texts, (), {e.paper_id: e.paper_id for e in texts}, {}, metadata
    )
    catalog = SimpleNamespace(
        documents={e.paper_id: SimpleNamespace(paper_id=e.paper_id) for e in texts},
        records=tuple(
            SimpleNamespace(
                name=e.paper_id, document=SimpleNamespace(paper_id=e.paper_id)
            )
            for e in texts
        ),
    )

    class Retriever:
        def __init__(self):
            self.calls = []

        def search_text(self, query, paper_ids, top_k):
            self.calls.append((query, paper_ids, top_k))
            return [
                RetrievedEvidence(evidence=e, retrieval_score=0.9)
                for e in texts
                if e.paper_id in paper_ids
            ][:top_k]

    request = CoverageComparisonRequest(
        case_id="test-coverage-comparison",
        question="Compare the model architectures.",
        paper_ids=[e.paper_id for e in texts],
        dimension="language_model",
    )
    config = {
        "runtime": {"max_retries": 0},
        "review_runtime": {},
        "recovery": {"max_research_model_calls": 100},
        "planner_mode": "fixed_benchmark_paper_dimension_plan_no_gold",
    }
    snapshot = {
        "snapshot_id": "synthetic",
        "corpus_validity_rate": 1.0,
        "evidence_provenance_validity_rate": 1.0,
    }
    return request, corpus, snapshot, config, Retriever(), catalog


def factory(
    *, reject_once=False, unknown=False, insufficient=False, fail_workers=False
):
    clients, roles, reviews = [], [], {}

    def make(role, task):
        roles.append(role)
        if role in ("worker", "recovery_worker"):

            def submit(messages, tools):
                current = context(messages)
                assignment = current["coverage_assignments"][0]
                if fail_workers:
                    raise ConnectionError("sk-secret /tmp/secret-model-context")
                is_gap = insufficient or (
                    unknown and len(current["recent_observations"]) > 1
                )
                return action(
                    "resolve_requirement",
                    requirement_id=assignment["requirement_id"],
                    status="insufficient_evidence" if is_gap else "supported",
                    coverage=[
                        {"aspect_id": aspect, "status": "missing"}
                        if is_gap
                        else {
                            "aspect_id": aspect,
                            "status": "covered",
                            "fact": {
                                "subject_paper_id": task.paper_id,
                                "dimension_id": "language_model",
                                "text": f"Synthetic {role} fact {aspect}.",
                                "evidence_ids": [
                                    "ev-unknown" if unknown else "ev-" + task.paper_id
                                ],
                            },
                        }
                        for aspect in ASPECTS_BY_DIMENSION["language_model"]
                    ],
                    stages=[],
                )

            responses = (
                [submit]
                if role == "recovery_worker"
                else [
                    action(
                        "search_text",
                        query="Synthetic local model",
                        paper_ids=[task.paper_id],
                        top_k=1,
                    ),
                    submit,
                    submit,
                ]
            )
        elif role == "reviewer":
            paper = task.requirement.paper_id
            prior = reviews.get(paper, 0)
            reviews[paper] = prior + 1
            rejected = reject_once and paper == "paper-a" and prior == 0
            responses = [
                action(
                    "submit_review",
                    status="rejected" if rejected else "passed",
                    reason_code="evidence_does_not_support_claim"
                    if rejected
                    else "evidence_supports_claim",
                )
            ]
        else:
            responses = [report]
        client = ScriptedLLMClient(responses)
        clients.append(client)
        return client

    return make, clients, roles


def execute(fixture, **modes):
    request, corpus, snapshot, config, retriever, catalog = fixture
    make, clients, roles = factory(**modes)
    result = asyncio.run(
        run_coverage_case(
            request,
            corpus,
            snapshot,
            config,
            retriever=retriever,
            client_factory=make,
            real_provider_used=False,
            catalog=catalog,
        )
    )
    return result, clients, roles, retriever


def test_actual_coverage_factory_reviewer_synthesis_and_full_observations(fixture):
    result, clients, roles, retriever = execute(fixture)
    assert result["status"] == "completed"
    assert result["outcome"]["report"]["status"] == "completed"
    assert len(result["outcome"]["report"]["verified_claims"]) == 2
    assert len(retriever.calls) == len(result["tool_observations"]) == 2
    assert result["outcome"]["metrics"]["recovery_attempted_count"] == 0
    assert all(
        d["worker_class"] == "CoverageAwarePaperWorker"
        for d in result["coverage_diagnostics"]
    )
    assert all(
        d["coverage"]["requirements"][0]["complete"]
        for d in result["coverage_diagnostics"]
    )
    assert (
        result["llm_provider_attempts_observed"]
        == sum(len(c.calls) for c in clients)
        == 7
    )
    assert roles.count("worker") == roles.count("reviewer") == 2
    for observation in result["tool_observations"]:
        text = observation["observation"]["payload"]["evidence"][0]
        assert text["text"].startswith("Synthetic prepared evidence")
        assert text["retrieval_score"] == 0.9
        assert observation["requested_tool_call_id"] == "synthetic-action"
    serialized = json.dumps(result)
    assert "private hidden chain of thought" not in serialized
    assert '"messages"' not in serialized
    assert all(item["active"] == 0 for item in result["resource_trace"].values())


def test_original_recovery_rechecks_with_new_coverage_worker(fixture):
    result, _, roles, _ = execute(fixture, reject_once=True)
    assert result["outcome"]["metrics"]["recovery_attempted_count"] == 1
    assert result["outcome"]["metrics"]["recovery_passed_count"] == 1
    assert roles.count("recovery_worker") == 1 and roles.count("reviewer") == 3
    assert len(result["coverage_diagnostics"]) == 3
    assert result["coverage_diagnostics"][-1]["phase"] == "recovery"
    assert (
        result["recovery_feedback"][0]["reason_code"]
        == "evidence_does_not_support_claim"
    )
    assert len(result["outcome"]["merged"]["history"]) == 6


def test_unobserved_citations_never_enter_report(fixture):
    result, _, _, _ = execute(fixture, unknown=True)
    assert not result["outcome"]["report"]["verified_claims"]
    assert all(
        r["status"] == "insufficient_evidence"
        for w in result["initial_worker_results"]
        for r in w["requirements"]
    )
    assert all(
        not r["evidence_ids"]
        for w in result["initial_worker_results"]
        for r in w["requirements"]
    )


def test_insufficiency_keeps_diagnostic_gaps_not_verified_facts(fixture):
    result, _, roles, _ = execute(fixture, insufficient=True)
    assert "reviewer" not in roles
    assert not result["outcome"]["report"]["verified_claims"]
    assert all(
        d["coverage"]["requirements"][0]["retained_gaps"]
        for d in result["coverage_diagnostics"]
    )


def test_dependency_failure_snapshots_are_not_abstentions(fixture):
    result, _, roles, _ = execute(fixture, fail_workers=True)
    assert all(w["status"] == "failed" for w in result["initial_worker_results"])
    assert all(
        r["status"] == "execution_failed"
        for w in result["initial_worker_results"]
        for r in w["requirements"]
    )
    assert "reviewer" not in roles
    assert result["llm_provider_attempts_observed"] == 4
    assert not result["outcome"]["report"]["verified_claims"]
    assert "sk-secret" not in json.dumps(result)


def test_real_mode_requires_existing_vision_adapter_before_calls(fixture):
    request, corpus, snapshot, config, retriever, catalog = fixture
    make, clients, _ = factory()
    with pytest.raises(ValueError, match="original_vision_client_factory"):
        asyncio.run(
            run_coverage_case(
                request,
                corpus,
                snapshot,
                config,
                retriever=retriever,
                client_factory=make,
                catalog=catalog,
            )
        )
    assert clients == [] and retriever.calls == []


def test_recorder_records_each_physical_attempt_and_redacts():
    class Executor:
        registry = None
        traces = ()

        def execute(self, name, arguments, *, tool_call_id=None):
            raise RuntimeError("sk-secret /tmp/private")

    records = []
    recorder = RecordingToolExecutor(
        Executor(), records, worker_task_id="w", paper_id="p", phase="initial"
    )
    for _ in range(2):
        with pytest.raises(RuntimeError):
            recorder.execute(
                "search_text", {"api_key": "secret"}, tool_call_id="recorded-call"
            )
    assert [r["physical_tool_attempt"] for r in records] == [1, 2]
    assert records[0]["exception_type"] == "RuntimeError"
    assert "sk-secret" not in json.dumps(records)
    assert records[0]["arguments"]["api_key"] != "secret"


def test_provider_export_drops_hidden_fields_recursively():
    client = SimpleNamespace(
        calls=[
            {
                "status": "success",
                "content": "hidden text",
                "reasoning": "hidden text",
                "tool_calls": [
                    {
                        "name": "search_text",
                        "arguments": {
                            "query": "public query",
                            "thought": "hidden text",
                            "nested": {
                                "chain_of_thought": "hidden text",
                                "internal_thought": "hidden text",
                            },
                        },
                    }
                ],
                "messages": [{"content": "hidden text"}],
            }
        ]
    )
    rows = _provider_events([(client, {"resource": "llm", "role": "worker"})])
    assert "hidden text" not in json.dumps(rows)
    assert rows[0]["tool_calls"][0]["arguments"]["query"] == "public query"


def test_vision_recorder_never_persists_image_prompt_or_response():
    class Vision:
        model = "synthetic-vision"

        def analyze(self, image_path, prompt):
            return {"text": "hidden response"}

    client = RecordingVisionClient(Vision())
    assert (
        client.analyze("/tmp/private.png", "hidden prompt")["text"] == "hidden response"
    )
    assert len(client.calls) == 1
    assert client.calls[0]["status"] == "success"
    assert "hidden" not in json.dumps(client.calls)
    assert "private.png" not in json.dumps(client.calls)


def test_cancellation_propagates_and_preserves_partial_counts(fixture):
    request, corpus, snapshot, config, retriever, catalog = fixture
    config["runtime"]["operation_timeout_seconds"] = 1

    class PendingClient:
        def __init__(self):
            self.calls = []

        async def complete(self, *, messages, tools):
            self.calls.append({"status": "pending"})
            await asyncio.sleep(10)

    clients = []

    def make(role, task):
        client = PendingClient()
        clients.append(client)
        return client

    async def scenario():
        running = asyncio.create_task(
            run_coverage_case(
                request,
                corpus,
                snapshot,
                config,
                retriever=retriever,
                client_factory=make,
                real_provider_used=False,
                catalog=catalog,
            )
        )
        for _ in range(100):
            if any(client.calls for client in clients):
                break
            await asyncio.sleep(0.001)
        running.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await running
        return caught.value.evaluation_snapshot

    result = asyncio.run(scenario())
    assert result["status"] == "cancelled"
    assert result["failure"]["issue_type"] == "CancelledError"
    assert result["llm_provider_attempts_observed"] > 0
    assert result["initial_worker_results"]
    assert all(w["status"] == "cancelled" for w in result["initial_worker_results"])
    assert all(item["active"] == 0 for item in result["resource_trace"].values())
