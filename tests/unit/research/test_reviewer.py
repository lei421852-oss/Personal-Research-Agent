"""Reviewer 只判断原 claim 的支持关系；身份和引用由程序绑定。"""

import asyncio
import json

import pytest
from test_review_validation import validated_task

from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.research.async_tools import AsyncLLMAdapter
from app.research.retry import RetryPolicy
from app.research.reviewer import EvidenceReviewer
from app.research.runtime import ResourceGate


def proposal(status="passed", reason="evidence_supports_claim", **extra):
    return LLMResponse(
        content="private hidden review reasoning",
        tool_calls=[
            ToolCall(
                id="review-1",
                name="submit_review",
                arguments={"status": status, "reason_code": reason} | extra,
            )
        ],
    )


def review_subject(responses, *, budget=3, client=None):
    client = client or ScriptedLLMClient(responses)
    delays = []

    async def sleeper(delay):
        delays.append(delay)

    adapter = AsyncLLMAdapter(
        client,
        ResourceGate(),
        retry_policy=RetryPolicy(
            max_retries=2, initial_delay=0.2, jitter_seconds=0, sleeper=sleeper
        ),
    )
    return (
        EvidenceReviewer(adapter, max_model_calls=budget, clock=lambda: 15.0),
        client,
        delays,
    )


class HTTPFailure(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("private provider failure /home/private/file sk-private-key")


def fail_with(error):
    def failure(messages, tools):
        raise error

    return failure


def assert_original_identity(result, task):
    assert result.worker_task_id == task.worker_task_id
    assert result.requirement_id == task.requirement.requirement_id
    assert result.paper_id == task.requirement.paper_id
    assert result.claim == task.requirement.claim
    assert result.evidence_ids == task.requirement.evidence_ids


def test_passed_review_uses_only_supplied_evidence_and_does_not_change_original():
    task = validated_task()
    before = task.model_dump_json()
    reviewer, client, _ = review_subject([proposal()])
    result = asyncio.run(reviewer.run(task))
    assert result.status == "passed" and result.reason_code == "evidence_supports_claim"
    assert result.validation_status == "passed" and result.issue is None
    assert_original_identity(result, task)
    assert task.model_dump_json() == before
    assert result.trace.model_calls == 1 and result.trace.retry_count == 0
    assert result.trace.latency_ms == 0
    call = client.calls[0]
    assert [s["name"] for s in call["tools"]] == ["submit_review"]
    schema = call["tools"][0]["input_schema"]
    assert set(schema["properties"]) == {"status", "reason_code"}
    assert schema["additionalProperties"] is False
    supplied = json.loads(call["messages"][-1]["content"])
    assert supplied["requirement"]["claim"] == task.requirement.claim
    assert [e["text"] for e in supplied["evidence"]] == [e.text for e in task.evidence]
    assert "private hidden review reasoning" not in result.model_dump_json()


@pytest.mark.parametrize(
    "reason", ["evidence_partially_supports_claim", "evidence_does_not_support_claim"]
)
def test_semantic_rejection_is_not_retried_or_used_to_rewrite_claim(reason):
    task = validated_task()
    reviewer, client, delays = review_subject(
        [proposal("rejected", reason), proposal()]
    )
    result = asyncio.run(reviewer.run(task))
    assert result.status == "rejected" and result.reason_code == reason
    assert_original_identity(result, task)
    assert len(client.calls) == result.trace.model_calls == 1
    assert result.trace.retry_count == 0 and delays == []


@pytest.mark.parametrize(
    "status,reason",
    [
        ("passed", "evidence_partially_supports_claim"),
        ("rejected", "evidence_supports_claim"),
        ("passed", "evidence_missing"),
        ("not_reviewable", "evidence_supports_claim"),
    ],
)
def test_invalid_semantic_verdict_pairs_are_protocol_failures_not_semantic_rejections(
    status, reason
):
    task = validated_task()
    reviewer, client, delays = review_subject([proposal(status, reason), proposal()])
    result = asyncio.run(reviewer.run(task))
    assert result.status == "not_reviewable" and result.validation_status == "passed"
    assert result.reason_code is None and result.issue.code == "protocol_failure"
    assert_original_identity(result, task)
    assert len(client.calls) == result.trace.model_calls == 1
    assert result.trace.retry_count == 0 and delays == []


@pytest.mark.parametrize(
    "extra",
    [
        {"claim": "Replacement claim"},
        {"paper_id": "replacement-paper"},
        {"requirement_id": "replacement-requirement"},
        {"evidence_ids": ["ev-fake"]},
        {"reasoning": "private illegal reasoning contents"},
    ],
)
def test_model_cannot_change_identity_claim_citations_or_supply_hidden_reasoning(extra):
    task = validated_task()
    reviewer, client, _ = review_subject([proposal(**extra), proposal()])
    result = asyncio.run(reviewer.run(task))
    assert result.status == "not_reviewable" and result.issue.code == "protocol_failure"
    assert_original_identity(result, task)
    assert len(client.calls) == 1
    assert "private illegal reasoning contents" not in result.model_dump_json()


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(content="A free-text review is not a verdict."),
        proposal().model_copy(update={"tool_calls": proposal().tool_calls * 2}),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="search-1",
                    name="search_text",
                    arguments={"query": "Fetch outside evidence"},
                )
            ]
        ),
    ],
)
def test_free_text_multiple_actions_and_research_tool_requests_are_not_executed(
    response,
):
    task = validated_task()
    reviewer, client, delays = review_subject([response, proposal()])
    result = asyncio.run(reviewer.run(task))
    assert result.status == "not_reviewable" and result.issue.code == "protocol_failure"
    assert_original_identity(result, task)
    assert len(client.calls) == 1 and delays == []


def test_transient_provider_failure_retries_only_at_adapter_boundary():
    task = validated_task()
    reviewer, client, delays = review_subject(
        [fail_with(HTTPFailure(429)), fail_with(HTTPFailure(503)), proposal()]
    )
    result = asyncio.run(reviewer.run(task))
    assert result.status == "passed"
    assert len(client.calls) == result.trace.model_calls == 3
    assert result.trace.retry_count == 2 and delays == [0.2, 0.4]
    assert_original_identity(result, task)


@pytest.mark.parametrize("status", [400, 401])
def test_permanent_provider_failure_is_not_retried_or_called_evidence_rejection(status):
    task = validated_task()
    reviewer, client, delays = review_subject(
        [fail_with(HTTPFailure(status)), proposal()]
    )
    result = asyncio.run(reviewer.run(task))
    assert result.status == "not_reviewable" and result.reason_code is None
    assert (
        result.validation_status == "passed"
        and result.issue.code == "dependency_failure"
    )
    assert len(client.calls) == result.trace.model_calls == 1
    assert result.trace.retry_count == 0 and delays == []
    assert_original_identity(result, task)
    serialized = result.model_dump_json()
    assert "sk-private-key" not in serialized and "/home/private" not in serialized


def test_retry_exhaustion_keeps_the_claim_reviewable_instead_of_semantically_rejected():
    task = validated_task()
    reviewer, client, delays = review_subject(
        [fail_with(TimeoutError())] * 3 + [proposal()]
    )
    result = asyncio.run(reviewer.run(task))
    assert result.status == "not_reviewable" and result.reason_code is None
    assert result.issue.code == "dependency_failure"
    assert len(client.calls) == result.trace.model_calls == 3
    assert result.trace.retry_count == 2 and delays == [0.2, 0.4]
    assert_original_identity(result, task)


def test_reviewer_budget_counts_physical_retry_calls():
    task = validated_task()
    reviewer, client, delays = review_subject([fail_with(TimeoutError())] * 3, budget=2)
    result = asyncio.run(reviewer.run(task))
    assert result.status == "not_reviewable" and result.reason_code is None
    assert result.issue.code == "budget_exhausted"
    assert len(client.calls) == result.trace.model_calls == 2
    assert result.trace.retry_count == 1 and delays == [0.2]


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_reviewer_budget_is_validated_before_any_provider_call(budget):
    with pytest.raises(ValueError):
        review_subject([], budget=budget)


def test_tampered_model_construct_review_task_cannot_reach_the_model():
    task = validated_task()
    wrong = task.evidence[0].model_copy(update={"paper_id": "another-paper"})
    tampered = task.model_copy(update={"evidence": [wrong]})
    reviewer, client, _ = review_subject([proposal()])
    result = asyncio.run(reviewer.run(tampered))
    assert result.status == "not_reviewable" and result.validation_status == "rejected"
    assert result.reason_code == "invalid_structure"
    assert not client.calls and result.trace.model_calls == 0


def test_native_reviewer_cancellation_propagates_with_typed_snapshot():
    async def scenario():
        entered = asyncio.Event()

        class Client:
            async def complete(self, *, messages, tools):
                entered.set()
                await asyncio.Event().wait()

        task = validated_task()
        reviewer, _, _ = review_subject([], client=Client())
        run = asyncio.create_task(reviewer.run(task))
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        result = reviewer.last_result
        assert result.status == "not_reviewable" and result.issue.code == "cancelled"
        assert_original_identity(result, task)
        assert result.trace.model_calls == 1
        assert reviewer.resource_gate.stats["llm"]["active"] == 0

    asyncio.run(scenario())
