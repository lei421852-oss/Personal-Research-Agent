"""有限纠正与基础设施重试各自计数，模型正文不进入执行 Trace。"""

import asyncio
import json

import pytest
from test_synthesis_input import proposal, source

from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.research.async_tools import AsyncLLMAdapter
from app.research.retry import RetryPolicy
from app.research.runtime import ResourceGate
from app.research.synthesis_agent import SynthesisAgent, SynthesisConfig


def response(arguments):
    return LLMResponse(
        content="private hidden synthesis reasoning",
        tool_calls=[
            ToolCall(id="synthesis-1", name="submit_report", arguments=arguments)
        ],
    )


def subject(responses, *, config=None, client=None):
    client = client or ScriptedLLMClient(responses)
    delays = []

    async def sleeper(delay):
        delays.append(delay)

    gate = ResourceGate()
    adapter = AsyncLLMAdapter(
        client,
        gate,
        retry_policy=RetryPolicy(
            2, initial_delay=0.1, jitter_seconds=0, sleeper=sleeper
        ),
    )
    return SynthesisAgent(adapter, config), client, delays


class HTTPFailure(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("private failure sk-private-key /home/private/file")


def fail(error):
    def operation(messages, tools):
        raise error

    return operation


def test_valid_single_action_and_unchanged_input():
    value = source()
    before = value.model_dump_json()
    agent, client, _ = subject([response(proposal(value))])
    outcome = asyncio.run(agent.run(value))
    assert outcome.status == "completed" and outcome.model_calls == 1
    assert value.model_dump_json() == before
    assert [s["name"] for s in client.calls[0]["tools"]] == ["submit_report"]
    assert "private hidden synthesis reasoning" not in outcome.model_dump_json()


def test_malformed_then_valid_uses_feedback_and_two_model_calls():
    value = source()
    agent, client, _ = subject(
        [
            response({"reasoning": "private malformed reasoning"}),
            response(proposal(value)),
        ]
    )
    result = asyncio.run(agent.run(value))
    assert result.status == "completed" and result.model_calls == 2
    assert result.protocol_corrections == 1 and result.provider_retries == 0
    context = json.loads(client.calls[1]["messages"][-1]["content"])
    assert context["protocol_feedback"]["issue_code"] == "invalid_structure"
    assert context["remaining_model_calls"] == 4
    assert "private malformed reasoning" not in result.model_dump_json() + json.dumps(
        context
    )


def test_invalid_citation_corrected_and_counted():
    value = source()
    bad = proposal(value)
    bad["sections"][0]["cells"][0]["evidence_ids"] = ["ev-fake"]
    agent, _, _ = subject([response(bad), response(proposal(value))])
    result = asyncio.run(agent.run(value))
    assert result.status == "completed" and result.citation_validation_failures == 1


def test_protocol_allowance_exhaustion_is_typed_degraded_outcome():
    agent, client, _ = subject([response({})] * 4)
    result = asyncio.run(agent.run(source()))
    assert result.status == "generation_degraded"
    assert result.issue.code == "protocol_correction_exhausted"
    assert result.model_calls == len(client.calls) == 3
    assert result.protocol_corrections == 2 and result.proposal is None


def test_model_budget_is_hard_limit_even_if_corrections_remain():
    agent, client, _ = subject(
        [response({})] * 3, config=SynthesisConfig(max_model_calls=1)
    )
    result = asyncio.run(agent.run(source()))
    assert (
        result.status == "generation_degraded"
        and result.issue.code == "budget_exhausted"
    )
    assert len(client.calls) == result.model_calls == 1


def test_multiple_actions_rejected_as_a_whole_not_first_selected():
    value = source()
    valid = response(proposal(value))
    multi = LLMResponse(tool_calls=valid.tool_calls * 2)
    agent, client, _ = subject([multi, valid])
    result = asyncio.run(agent.run(value))
    assert result.protocol_corrections == 1 and len(client.calls) == 2
    assert result.trace[0]["issue_code"] == "exactly_one_report_action"


@pytest.mark.parametrize("status", [429, 503])
def test_transient_provider_retry_is_not_protocol_correction(status):
    value = source()
    agent, client, delays = subject(
        [fail(HTTPFailure(status)), response(proposal(value))]
    )
    result = asyncio.run(agent.run(value))
    assert result.status == "completed"
    assert len(client.calls) == result.model_calls == 2
    assert result.provider_retries == 1 and result.protocol_corrections == 0
    assert delays == [0.1]


@pytest.mark.parametrize("status", [400, 401])
def test_permanent_provider_failure_has_no_retry_or_protocol_correction(status):
    agent, client, delays = subject([fail(HTTPFailure(status)), response({})])
    result = asyncio.run(agent.run(source()))
    assert (
        result.status == "generation_degraded"
        and result.issue.code == "dependency_failure"
    )
    assert len(client.calls) == result.model_calls == 1
    assert result.provider_retries == result.protocol_corrections == 0 and delays == []
    assert "sk-private-key" not in result.model_dump_json()


def test_provider_exhaustion_keeps_separate_retry_counts():
    agent, client, delays = subject([fail(TimeoutError())] * 3)
    result = asyncio.run(agent.run(source()))
    assert result.status == "generation_degraded"
    assert result.model_calls == len(client.calls) == 3
    assert result.provider_retries == 2 and result.protocol_corrections == 0
    assert delays == [0.1, 0.2]


def test_retry_then_malformed_then_success_shares_physical_budget():
    value = source()
    agent, _, _ = subject(
        [fail(TimeoutError()), response({}), response(proposal(value))],
        config=SynthesisConfig(max_model_calls=3),
    )
    result = asyncio.run(agent.run(value))
    assert result.status == "completed" and result.model_calls == 3
    assert result.provider_retries == result.protocol_corrections == 1


def test_no_verified_evidence_skips_provider_even_if_client_would_fail():
    statuses = {
        (p, d): "insufficient_evidence"
        for p in ["paper-a", "paper-b", "paper-c"]
        for d in ["architecture", "training"]
    }
    agent, client, delays = subject([fail(AssertionError("Provider must not run"))])
    result = asyncio.run(agent.run(source(statuses=statuses)))
    assert result.status == "insufficient_evidence"
    assert not client.calls and not delays and result.model_calls == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_model_calls": 0},
        {"max_model_calls": True},
        {"max_protocol_corrections": -1},
        {"max_protocol_corrections": True},
    ],
)
def test_budget_configuration_is_strict(kwargs):
    with pytest.raises(ValueError):
        SynthesisConfig(**kwargs)
