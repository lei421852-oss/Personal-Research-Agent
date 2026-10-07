"""多动作响应只能通过有限的策略纠正恢复，不执行、不挑选其中任何动作。"""

import asyncio
import json

import pytest
from pydantic import ValidationError
from test_paper_worker import (
    EV,
    action,
    last_context,
    resolve,
    search,
    subject,
    task,
)

from app.llm.models import LLMResponse
from app.research.runtime import ResearchRuntimeConfig


def multiple(*responses):
    calls = [call for response in responses for call in response.tool_calls]
    return LLMResponse(
        content="private hidden multi-action reasoning",
        tool_calls=[
            call.model_copy(deep=True, update={"id": f"multiple-{index}"})
            for index, call in enumerate(calls)
        ],
    )


def multiple_searches():
    return multiple(search("Rejected search A"), search("Rejected search B"))


def rejected_steps(result):
    return [
        step
        for step in result.trace.actions
        if step.get("issue_code") == "multiple_actions"
    ]


def assert_not_executed(result, count):
    rejected = rejected_steps(result)
    assert len(rejected) == count
    assert all(step["execution_status"] == "not_executed" for step in rejected)
    assert result.trace.retry_count == 0


def test_multiple_actions_then_corrected_search_and_resolution_complete():
    worker, client, tool = subject(
        [multiple_searches(), search("Corrected single search"), resolve()]
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert result.requirements[0].status == "supported"
    assert result.requirements[0].evidence_ids == [EV.evidence_id]
    assert [call.query for call in tool.calls] == ["Corrected single search"]
    assert len(client.calls) == result.trace.model_calls == 3
    assert result.trace.tool_calls == 1
    assert result.trace.protocol_corrections == 1
    assert_not_executed(result, 1)
    assert result.trace.actions[1]["protocol_recovery"] is True
    assert not result.trace.actions[2].get("protocol_recovery", False)


def test_limit_one_terminates_on_second_rejection_without_executing_tools():
    worker, client, tool = subject(
        [multiple_searches(), multiple_searches(), search(), resolve()],
        config=ResearchRuntimeConfig(max_protocol_corrections=1),
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "failed"
    assert result.error.code == "protocol_correction_exhausted"
    assert all(r.status == "execution_failed" for r in result.requirements)
    assert len(client.calls) == result.trace.model_calls == 2
    assert not tool.calls and result.trace.tool_calls == 0
    assert result.trace.protocol_corrections == 1
    assert_not_executed(result, 2)


def test_default_two_allows_correction_after_two_rejected_responses():
    config = ResearchRuntimeConfig()
    assert config.max_protocol_corrections == 2
    worker, client, tool = subject(
        [
            multiple_searches(),
            multiple_searches(),
            resolve(status="insufficient_evidence"),
        ],
        config=config,
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and not tool.calls
    assert result.requirements[0].status == "insufficient_evidence"
    assert len(client.calls) == result.trace.model_calls == 3
    assert result.trace.protocol_corrections == 2
    assert_not_executed(result, 2)
    assert result.trace.actions[-1]["protocol_recovery"] is True


def test_default_two_exhausts_only_on_third_rejected_response():
    worker, client, tool = subject(
        [multiple_searches()] * 3 + [resolve(status="insufficient_evidence")]
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed"
    assert result.error.code == "protocol_correction_exhausted"
    assert len(client.calls) == result.trace.model_calls == 3
    assert not tool.calls and result.trace.tool_calls == 0
    assert result.trace.protocol_corrections == 2
    assert_not_executed(result, 3)


def test_zero_corrections_rejects_first_multi_action_response_immediately():
    worker, client, tool = subject(
        [multiple_searches(), resolve(status="insufficient_evidence")],
        config=ResearchRuntimeConfig(max_protocol_corrections=0),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed"
    assert result.error.code == "protocol_correction_exhausted"
    assert len(client.calls) == result.trace.model_calls == 1
    assert not tool.calls and result.trace.protocol_corrections == 0
    assert_not_executed(result, 1)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_correction_limit_is_a_strict_nonnegative_integer(value):
    with pytest.raises(ValidationError):
        ResearchRuntimeConfig(max_protocol_corrections=value)


def test_model_budget_still_ends_pending_requirements_after_rejection():
    worker, client, tool = subject(
        [multiple_searches(), resolve(status="insufficient_evidence")],
        config=ResearchRuntimeConfig(max_model_calls_per_worker=1),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed" and result.error.code == "budget_exhausted"
    assert result.requirements[0].status == "execution_failed"
    assert len(client.calls) == result.trace.model_calls == 1
    assert not tool.calls and result.trace.tool_calls == 0
    assert_not_executed(result, 1)


def test_rejection_does_not_consume_remaining_tool_budget():
    worker, client, tool = subject(
        [multiple_searches(), search(), resolve()],
        config=ResearchRuntimeConfig(max_tool_calls_per_worker=1),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert len(tool.calls) == result.trace.tool_calls == 1
    context = json.loads(client.calls[1]["messages"][-1]["content"])
    assert context["remaining_budget"]["tool_calls"] == 1
    assert context["remaining_budget"]["model_calls"] == 19
    assert result.trace.retry_count == 0


def test_recovery_can_resolve_from_existing_evidence_after_tool_budget_is_used():
    worker, _, tool = subject(
        [search(), multiple(search("Do not run this search"), resolve()), resolve()],
        config=ResearchRuntimeConfig(max_tool_calls_per_worker=1),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert len(tool.calls) == result.trace.tool_calls == 1
    assert result.trace.model_calls == 3
    assert result.requirements[0].evidence_ids == [EV.evidence_id]
    assert_not_executed(result, 1)
    assert result.trace.actions[-1]["protocol_recovery"] is True


def test_already_supported_requirement_survives_correction_exhaustion():
    worker, client, tool = subject(
        [search(), resolve(0), multiple_searches(), multiple_searches(), resolve(1)],
        config=ResearchRuntimeConfig(max_protocol_corrections=1),
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "partial_success"
    assert result.error.code == "protocol_correction_exhausted"
    assert [r.status for r in result.requirements] == ["supported", "execution_failed"]
    assert result.requirements[0].evidence_ids == [EV.evidence_id]
    assert result.error.requirement_ids == ["req-paper-local-1"]
    assert len(client.calls) == result.trace.model_calls == 4
    assert len(tool.calls) == result.trace.tool_calls == 1
    assert_not_executed(result, 2)


def test_feedback_is_structured_and_present_in_the_next_policy_context():
    worker, client, _ = subject(
        [multiple_searches(), resolve(status="insufficient_evidence")]
    )
    result = asyncio.run(worker.run(task()))
    context = last_context(client)
    feedback = context["recent_observations"][-1]
    assert feedback["status"] == "error"
    payload = feedback["payload"]
    assert payload["issue_code"] == "multiple_actions"
    assert payload["expected"] == "exactly_one_action"
    assert payload["actual_action_count"] == 2
    assert isinstance(payload["recovery_instruction"], str)
    assert payload["recovery_instruction"].strip()
    assert context["remaining_budget"]["protocol_corrections"] == 1
    assert result.trace.retry_count == 0


def test_multi_resolution_has_no_side_effects_until_corrected_single_batch():
    batch = action(
        "resolve_requirements",
        resolutions=[
            resolve(0).tool_calls[0].arguments,
            resolve(1).tool_calls[0].arguments,
        ],
    )
    worker, client, tool = subject([search(), multiple(resolve(0), resolve(1)), batch])
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed"
    assert all(r.status == "supported" for r in result.requirements)
    context = last_context(client)
    assert context["resolved_requirements"] == []
    assert len(context["unresolved_requirements"]) == 2
    assert len(tool.calls) == result.trace.tool_calls == 1
    assert_not_executed(result, 1)
    assert result.trace.actions[-1]["protocol_recovery"] is True


def test_correction_budget_is_not_reset_by_an_intermediate_valid_action():
    worker, client, tool = subject(
        [
            multiple_searches(),
            search(),
            multiple_searches(),
            resolve(0),
            multiple_searches(),
            resolve(1),
        ]
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "partial_success"
    assert result.error.code == "protocol_correction_exhausted"
    assert result.trace.protocol_corrections == 2
    assert len(client.calls) == result.trace.model_calls == 5
    assert len(tool.calls) == 1
    assert_not_executed(result, 3)


def test_zero_actions_retains_existing_immediate_protocol_failure():
    worker, client, tool = subject(
        [
            LLMResponse(content="Unstructured answer"),
            resolve(status="insufficient_evidence"),
        ]
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed" and result.error.code == "protocol_failure"
    assert result.requirements[0].status == "execution_failed"
    assert len(client.calls) == result.trace.model_calls == 1
    assert not tool.calls and result.trace.protocol_corrections == 0


def test_rejected_action_arguments_and_hidden_model_content_are_not_published():
    invalid_actions = multiple(
        action("search_text", reasoning="private discarded action rationale"),
        action("unknown_tool", chain_of_thought="private discarded chain contents"),
    )
    worker, client, tool = subject(
        [invalid_actions, resolve(status="insufficient_evidence")]
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and not tool.calls
    assert len(client.calls) == result.trace.model_calls == 2
    assert_not_executed(result, 1)
    serialized = result.model_dump_json() + json.dumps(last_context(client))
    for forbidden in [
        "private hidden multi-action reasoning",
        "private discarded action rationale",
        "private discarded chain contents",
    ]:
        assert forbidden not in serialized
