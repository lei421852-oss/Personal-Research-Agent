"""主入口协议纠正是策略恢复，不执行被拒动作，也不使用 Provider 重试。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.llm.models import LLMResponse, ToolCall
from app.research.react_budget import UnifiedRunBudget
from app.research.react_policy import MainPolicyClient
from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.research_handoff import ResearchContext
from app.research.run_manager import MainAgentRunRequest
from tests.test_react_workbench import (
    CATALOG,
    Dependencies,
    PaperSearch,
    factory,
    finish,
    response,
)


def batch(count=2):
    return LLMResponse(
        content="private thought must not be forwarded",
        tool_calls=[
            ToolCall(
                id=f"rejected-{i}",
                name="search_papers",
                arguments={"query": "discovery"},
            )
            for i in range(count)
        ],
    )


def policy(sequence, **kwargs):
    calls, events = [], []

    class Client:
        def complete(self, *, messages, tools):
            calls.append(messages)
            return sequence[len(calls) - 1]

    context = ResearchContext("Original task", "Original task", CATALOG, set(), [])
    client = MainPolicyClient(
        Client(),
        context,
        UnifiedRunBudget(20),
        emit=lambda **e: events.append(e),
        **kwargs,
    )
    return client, calls, events


def complete(client):
    return client.complete(
        messages=[
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "Original task"},
        ],
        tools=[{"name": "finish", "input_schema": {}}],
    )


@pytest.mark.parametrize("count", [0, 2, 4])
def test_rejected_response_feedback_and_single_action_recovery(count):
    accepted = response(*finish(), "accepted")
    client, calls, events = policy([batch(count), accepted])
    assert complete(client) is accepted
    assert client.decision_steps == 2
    assert client.protocol_corrections == 1
    assert "exactly_one_action" in calls[1][0]["content"]
    assert f'"actual_action_count": {count}' in calls[1][0]["content"]
    assert "private thought" not in json.dumps(calls)
    assert [e["details"]["protocol_feedback"]["status"] for e in events] == [
        "rejected",
        "recovered",
    ]
    assert client.budget.tools_used == 0


def test_exhaustion_returns_whole_rejected_batch_to_strict_controller():
    rejected = batch()
    client, calls, _ = policy([rejected] * 3)
    assert complete(client) is rejected
    assert len(calls) == 3
    assert client.protocol_corrections == 2
    assert client.budget.tools_used == 0


def test_corrections_share_decision_budget_across_successful_turns():
    client, calls, _ = policy(
        [batch(), response(*finish(), "accepted"), batch()], max_decision_steps=3
    )
    assert len(complete(client).tool_calls) == 1
    assert len(complete(client).tool_calls) == 2
    with pytest.raises(RuntimeError, match="decision budget exhausted"):
        complete(client)
    assert len(calls) == 3


def test_disabled_correction_still_rejects_without_selection():
    rejected = batch()
    client, calls, _ = policy([rejected], max_protocol_corrections=0)
    assert complete(client) is rejected
    assert len(calls) == 1


def test_runtime_recovery_executes_only_the_model_reselected_action():
    async def scenario():
        deps, search, events = Dependencies(), PaperSearch(), []
        base, vision, _, calls, _ = factory(
            deps,
            [
                ("search_papers", {"query": "Discover related papers", "top_k": 2}),
                finish("metadata", paper_ids=search.ids),
            ],
        )
        rejected = False

        class Client(base):
            def complete(self, *, messages, tools):
                nonlocal rejected
                if not rejected and any(
                    t["name"] == "run_multi_agent_research" for t in tools
                ):
                    rejected = True
                    return batch()
                return super().complete(messages=messages, tools=tools)

        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=Client,
            vision_factory=vision,
            paper_retriever=search,
            paper_catalog=CATALOG,
        )
        result = await runtime.run(
            MainAgentRunRequest(question="Discover related papers"),
            lambda **e: events.append(e),
        )
        return SimpleNamespace(result=result, search=search, events=events, calls=calls)

    case = asyncio.run(scenario())
    assert case.result["status"] == "completed"
    assert len(case.search.calls) == 1
    metrics = case.result["answer_first"]["metrics"]
    assert metrics["tool_calls"] == 1
    assert metrics["model_calls"] == 4  # 拒绝、合法检索、finish、中文展示各一次。
    assert metrics["main_decision_steps"] == 3
    assert metrics["main_protocol_corrections"] == 1
    assert "Controller protocol observation" in case.calls[0][0]["content"]
    assert [e["status"] for e in case.result["main_agent"]["protocol_trace"]] == [
        "rejected",
        "recovered",
    ]
