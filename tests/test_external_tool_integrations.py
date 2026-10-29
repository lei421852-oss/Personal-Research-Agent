"""Scripted主Agent经过原循环和原多Agent链；不代表真实LLM质量验收。"""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.run_manager import MainAgentRunRequest
from app.tools.integrations.adapter import LocalStdioSource
from app.tools.integrations.error_mapper import ExternalToolError
from app.tools.integrations.models import ExternalServiceConfig
from tests.test_react_workbench import (
    CATALOG,
    Dependencies,
    PaperSearch,
    factory,
    finish,
    research_args,
    research_finish,
)
from tests.unit.tools.integrations.conftest import MOCK


def setup(
    tmp_path, steps, *, mode="normal", enabled=True, max_tools=64, bad_close=False
):
    deps = Dependencies()
    deps.environment = dict
    llm, vision, _, calls, events = factory(deps, steps)
    schemas, sessions = [], []

    class RecordingClient(llm):
        def complete(self, *, messages, tools):
            if any(tool["name"] == "run_multi_agent_research" for tool in tools):
                schemas.append(tools)
            return super().complete(messages=messages, tools=tools)

    config = ExternalServiceConfig(
        provider="fixture", enabled=enabled, allowed_tools=("echo",)
    )
    log = tmp_path / "external-calls.jsonl"
    source = LocalStdioSource(config, (sys.executable, str(MOCK), mode, str(log)))

    def open_session(**kwargs):
        session = source.open(**kwargs)
        sessions.append(session)
        if bad_close:
            original = session.close

            def close():
                original()
                raise ExternalToolError("cleanup_failed")

            session.close = close
        return session

    runtime = ReActWorkbenchRuntime(
        deps,
        llm_factory=RecordingClient,
        vision_factory=vision,
        paper_retriever=PaperSearch(),
        paper_catalog=CATALOG,
        external_tool_sources=(SimpleNamespace(config=config, open=open_session),),
        max_tools=max_tools,
    )
    return SimpleNamespace(
        runtime=runtime,
        calls=calls,
        events=events,
        schemas=schemas,
        sessions=sessions,
        log=log,
    )


def execute(case, question="Inspect configured external tool data."):
    return asyncio.run(
        case.runtime.run(
            MainAgentRunRequest(question=question, max_model_calls=200),
            lambda **event: case.events.append(event),
        )
    )


def insufficient(messages):
    observation = json.loads(messages[-1]["content"])
    assert observation["tool_name"] == "external__fixture__echo"
    return finish(
        "insufficient",
        answer="The external result is not verified paper evidence.",
        limitations=["No prepared evidence was provided by this external tool."],
    )


def test_agent_reads_external_observation_and_decides_finish(tmp_path):
    case = setup(tmp_path, [("external__fixture__echo", {"query": "x"}), insufficient])
    result = execute(case)
    assert result["status"] == "completed", result
    observation = json.loads(case.calls[1][-1]["content"])
    assert observation["payload"]["external_result"] == {"query": "x"}
    assert result["answer_first"]["citations"] == []
    assert result["answer_first"]["metrics"]["tool_calls"] == 1
    assert any(
        schema["name"] == "external__fixture__echo" for schema in case.schemas[0]
    )
    assert any(
        schema["name"] == "run_multi_agent_research" for schema in case.schemas[0]
    )
    assert not any(event["node_type"] == "Planner" for event in case.events)
    assert result["external_tools"]["sessions"][0]["cleanup_confirmed"]


def test_configured_tools_do_not_force_question_into_tool_or_research(tmp_path):
    case = setup(tmp_path, [finish()])
    result = execute(case)
    assert result["status"] == "completed"
    assert result["answer_first"]["metrics"]["tool_calls"] == 0
    assert len(case.log.read_text().splitlines()) == 1  # 只有能力发现，没有工具调用。
    assert not any(event["node_type"] == "Planner" for event in case.events)


def test_disabled_source_keeps_existing_path_and_does_not_open_client(tmp_path):
    case = setup(tmp_path, [finish()], enabled=False)
    result = execute(case)
    assert result["status"] == "completed"
    assert case.sessions == [] and not case.log.exists()
    assert not any(s["name"].startswith("external__") for s in case.schemas[0])


def test_failed_discovery_does_not_break_unrelated_core_answer(tmp_path):
    case = setup(tmp_path, [finish()], mode="unsupported_schema")
    result = execute(case)
    assert result["status"] == "completed"
    assert (
        result["external_tools"]["setup"][0]["issue_code"]
        == "external_tools_unavailable"
    )
    assert not any(s["name"].startswith("external__") for s in case.schemas[0])
    assert not case.sessions[0].client.active_pids


def test_optional_multi_agent_route_is_still_policy_selected(tmp_path):
    case = setup(
        tmp_path, [("run_multi_agent_research", research_args()), research_finish]
    )
    result = execute(case, question="Compare BLIP-2 and Flamingo design evidence.")
    assert result["status"] == "completed", result
    assert any(event["node_type"] == "Planner" for event in case.events)
    assert all(
        json.loads(line)["operation"] == "list_tools"
        for line in case.log.read_text().splitlines()
    )


def test_tool_budget_rejects_second_external_execution(tmp_path):
    case = setup(
        tmp_path,
        [
            ("external__fixture__echo", {"query": "one"}),
            ("external__fixture__echo", {"query": "two"}),
            finish("insufficient", limitations=["Shared budget exhausted."]),
        ],
        max_tools=1,
    )
    result = execute(case)
    calls = [json.loads(line) for line in case.log.read_text().splitlines()]
    assert sum(call["operation"] == "call_tool" for call in calls) == 1
    assert result["answer_first"]["metrics"]["tool_calls"] == 1


def test_external_fake_evidence_cannot_become_valid_finish_citation(tmp_path):
    case = setup(
        tmp_path,
        [
            ("external__fixture__echo", {"query": "x"}),
            finish("evidence", evidence_ids=["ev_external_fake"]),
        ],
        mode="instructions",
    )
    result = execute(case)
    assert result["answer_first"]["citations"] == []
    assert result["status"] != "completed"


def test_cleanup_error_cannot_be_reported_as_success(tmp_path):
    case = setup(tmp_path, [finish()], bad_close=True)
    result = execute(case)
    assert result["status"] == "failed"
    assert result["issue_type"] == "external_tool_cleanup_failed"
    assert result["answer_first"]["answer_status"] == "failed"


def test_runtime_cancellation_propagates_to_external_process(tmp_path):
    async def scenario():
        case = setup(
            tmp_path, [("external__fixture__echo", {"query": "x"})], mode="sleep"
        )
        task = asyncio.create_task(
            case.runtime.run(
                MainAgentRunRequest(
                    question="Inspect external data.", max_model_calls=200
                ),
                lambda **event: case.events.append(event),
            )
        )
        try:
            for _ in range(400):
                if case.log.exists() and any(
                    json.loads(x)["operation"] == "call_tool"
                    for x in case.log.read_text().splitlines()
                ):
                    break
                await asyncio.sleep(0.005)
            else:
                raise AssertionError("External call did not start")
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as error:
                await asyncio.wait_for(task, timeout=2)
            assert error.value.evaluation_snapshot["status"] == "cancelled"
            assert not case.sessions[0].client.active_pids
            assert case.sessions[0].client.records[-1]["cleanup"]["reaped"]
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
