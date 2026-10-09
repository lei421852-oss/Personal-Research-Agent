"""协议提示与反馈可以增强，动作验证、引用与预算边界不得放宽。"""

import asyncio
import json
from copy import deepcopy

import pytest
from test_paper_worker import (
    PAPER,
    action,
    last_context,
    resolve,
    search,
    subject,
    task,
)
from test_worker_protocol_recovery import multiple_searches

from app.research.runtime import ResearchRuntimeConfig
from app.research.worker import PaperResearchWorker
from app.research.worker_protocol import WorkerProtocolAdapter, protocol_request


def wrapped(responses, **kwargs):
    worker, client, tool = subject(responses, **kwargs)
    worker.llm = WorkerProtocolAdapter(
        worker.llm,
        action_source=lambda: (
            worker.state.actions[-1] if worker.state and worker.state.actions else None
        ),
    )
    return worker, client, tool


def test_single_action_runs_original_worker_and_preserves_citation():
    worker, client, tool = wrapped([search(), resolve()])
    result = asyncio.run(worker.run(task()))
    assert type(worker) is PaperResearchWorker
    assert result.status == "completed" and result.requirements[0].status == "supported"
    assert len(tool.calls) == result.trace.tool_calls == 1
    assert result.trace.model_calls == 2
    assert worker.llm.resource_gate is worker.tools.resource_gate
    assert worker.llm.snapshot() == worker.llm.adapter.snapshot()
    assert not worker.llm.feedback_events
    assert last_context(client)["worker_protocol"]["actions_per_response"] == 1


def test_schema_view_is_paper_local_but_input_and_original_schema_are_immutable():
    worker, _, _ = subject([])
    tools = worker._schemas()
    before = deepcopy(tools)
    messages = [
        {"role": "system", "content": "Original policy"},
        {
            "role": "user",
            "content": json.dumps({"paper_id": PAPER, "recent_observations": []}),
        },
    ]
    unchanged = deepcopy(messages)
    updated, schema, _ = protocol_request(messages, tools)
    assert tools == before and messages == unchanged
    search_schema = schema[0]["input_schema"]
    assert "paper_ids" in search_schema["required"]
    assert search_schema["properties"]["paper_ids"]["type"] == "array"
    assert search_schema["properties"]["paper_ids"]["items"]["enum"] == [PAPER]
    assert search_schema["additionalProperties"] is False
    assert "Original policy" in updated[0]["content"]
    example = json.loads(updated[-1]["content"])["worker_protocol"]["valid_examples"][
        "search_text"
    ]
    validated = worker.tools.registry.get("search_text").input_model.model_validate(
        example["arguments"]
    )
    assert validated.paper_ids == [PAPER]


def test_multi_action_rejected_then_feedback_then_single_action():
    def correct(messages, tools):
        ctx = json.loads(messages[-1]["content"])
        feedback = ctx["protocol_feedback"][0]
        assert feedback["issue_code"] == "multiple_actions"
        assert (
            feedback["actual_action_count"] == 2
            and feedback["expected_action_count"] == 1
        )
        assert feedback["valid_example"]["arguments"]["paper_ids"] == [PAPER]
        assert ctx["remaining_budget"]["protocol_corrections"] == 1
        assert ctx["remaining_budget"]["tool_calls"] == 8
        return search("Chosen corrected query")

    worker, client, tool = wrapped([multiple_searches(), correct, resolve()])
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert [c.query for c in tool.calls] == ["Chosen corrected query"]
    assert result.trace.model_calls == 3 and result.trace.tool_calls == 1
    assert result.trace.protocol_corrections == 1 and result.trace.retry_count == 0
    assert result.trace.actions[0]["execution_status"] == "not_executed"
    assert last_context(client)["protocol_feedback"] == []


def test_wrong_field_is_rejected_and_model_uses_example_to_recover():
    def correct(messages, tools):
        ctx = json.loads(messages[-1]["content"])
        feedback = ctx["protocol_feedback"][0]
        assert feedback["issue_code"] == "invalid_arguments"
        assert any(e["field"] == ["paper_id"] for e in feedback["field_errors"])
        assert "paper_id" not in feedback["argument_contract"]["allowed_fields"]
        arguments = deepcopy(feedback["valid_example"]["arguments"])
        arguments["query"] = "Model explicitly corrects its original query"
        return action("search_text", **arguments)

    worker, _, tool = wrapped(
        [action("search_text", query="q", paper_id=PAPER), correct, resolve()]
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert result.trace.actions[0]["issue_code"] == "invalid_arguments"
    assert result.trace.tool_calls == len(tool.calls) == 1
    assert result.trace.model_calls == 3 and result.trace.protocol_corrections == 0
    assert tool.calls[0].paper_ids == [PAPER]


def test_adapter_returns_all_proposed_actions_without_selection_or_rewriting():
    response = multiple_searches()

    class Adapter:
        resource_gate = object()

        async def complete(self, **kwargs):
            assert kwargs["attempt_budget"] == 3
            return response

    adapter = WorkerProtocolAdapter(Adapter())
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": '{"paper_id":"p"}'},
    ]
    assert (
        asyncio.run(adapter.complete(messages=messages, tools=[], attempt_budget=3))
        is response
    )
    assert len(response.tool_calls) == 2


def test_correction_exhaustion_still_fails_without_tools_or_fake_insufficiency():
    worker, _, tool = wrapped([multiple_searches()] * 3)
    result = asyncio.run(worker.run(task()))
    assert (
        result.status == "failed"
        and result.error.code == "protocol_correction_exhausted"
    )
    assert result.requirements[0].status == "execution_failed"
    assert result.trace.protocol_corrections == 2
    assert (
        result.trace.model_calls == 3
        and result.trace.tool_calls == 0
        and not tool.calls
    )


def test_invalid_arguments_are_bounded_by_existing_model_budget():
    invalid = action("search_text", query="q", paper_id=PAPER)
    worker, _, tool = wrapped(
        [invalid] * 3, config=ResearchRuntimeConfig(max_model_calls_per_worker=3)
    )
    result = asyncio.run(worker.run(task()))
    assert result.error.code == "budget_exhausted"
    assert result.requirements[0].status == "execution_failed" and not tool.calls
    assert result.trace.model_calls == 3 and result.trace.protocol_corrections == 0


def test_legitimate_insufficiency_remains_business_result_without_retrieval():
    worker, _, tool = wrapped([resolve(status="insufficient_evidence")])
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and result.error is None and not tool.calls
    assert result.requirements[0].status == "insufficient_evidence"


def test_foreign_scope_is_not_silently_rewritten():
    worker, _, tool = wrapped(
        [search(paper_id="peer"), resolve(status="insufficient_evidence")]
    )
    result = asyncio.run(worker.run(task()))
    assert (
        result.trace.actions[0]["issue_code"] == "invalid_arguments" and not tool.calls
    )
    assert result.trace.actions[0]["arguments"]["paper_ids"] == ["peer"]


def test_unobserved_citation_is_not_accepted_by_protocol_adapter():
    worker, _, tool = wrapped(
        [
            resolve(evidence_ids=["not-observed"]),
            resolve(status="insufficient_evidence"),
        ]
    )
    result = asyncio.run(worker.run(task()))
    assert result.trace.actions[0]["status"] == "error"
    assert result.requirements[0].status == "insufficient_evidence" and not tool.calls


def test_resolved_progress_survives_protocol_exhaustion():
    worker, _, _ = wrapped([search(), resolve(0)] + [multiple_searches()] * 3)
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "partial_success"
    assert [r.status for r in result.requirements] == ["supported", "execution_failed"]
    assert result.trace.tool_calls == 1


def test_cancellation_keeps_original_semantics():
    from app.llm.fake import ScriptedLLMClient

    async def scenario():
        started = asyncio.Event()

        class Waiting(ScriptedLLMClient):
            async def complete(self, **kwargs):
                started.set()
                await asyncio.Event().wait()

        worker, _, _ = wrapped([], client=Waiting([]))
        running = asyncio.create_task(worker.run(task()))
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert worker.last_result.status == "cancelled"
        assert worker.last_result.requirements[0].status == "cancelled"
        await worker.drain()

    asyncio.run(scenario())


def test_timeout_remains_execution_failure_and_uses_no_tool_budget():
    from app.llm.fake import ScriptedLLMClient

    class Waiting(ScriptedLLMClient):
        async def complete(self, **kwargs):
            await asyncio.Event().wait()

    worker, _, tool = wrapped(
        [],
        client=Waiting([]),
        config=ResearchRuntimeConfig(operation_timeout_seconds=0.01, max_retries=0),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed"
    assert result.requirements[0].status == "execution_failed"
    assert result.trace.model_calls == 1 and result.trace.tool_calls == 0
    assert not tool.calls
