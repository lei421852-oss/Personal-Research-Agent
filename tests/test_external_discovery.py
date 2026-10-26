"""通过原主Agent循环执行Scripted动作；不声称真实模型自主决策已验收。"""

import asyncio
import json
from types import SimpleNamespace

from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.run_manager import MainAgentRunRequest
from tests.test_react_workbench import (
    CATALOG,
    PAPERS,
    Dependencies,
    PaperSearch,
    factory,
    finish,
    research_args,
    research_finish,
)
from tests.unit.papers.test_arxiv import IDENTITY, Transport, client, feed


def run(
    steps,
    *,
    api=None,
    environment=None,
    question="Find external preprints.",
    max_tools=64,
):
    deps = Dependencies()
    deps.environment = lambda: environment or {}
    llm, vision, _created, calls, events = factory(deps, steps)
    schemas = []

    class RecordingClient(llm):
        def complete(self, *, messages, tools):
            if any(t["name"] == "run_multi_agent_research" for t in tools):
                schemas.append(tools)
            return super().complete(messages=messages, tools=tools)

    runtime = ReActWorkbenchRuntime(
        deps,
        llm_factory=RecordingClient,
        vision_factory=vision,
        paper_retriever=PaperSearch(),
        paper_catalog=CATALOG,
        arxiv_client=api,
        max_tools=max_tools,
    )
    result = asyncio.run(
        runtime.run(
            MainAgentRunRequest(question=question, max_model_calls=200),
            lambda **e: events.append(e),
        )
    )
    return SimpleNamespace(
        result=result, calls=calls, events=events, schemas=schemas, deps=deps
    )


def metadata_finish(messages):
    papers = json.loads(messages[-1]["content"])["payload"]["papers"]
    return finish("metadata", paper_ids=[p["paper_id"] for p in papers])


def test_external_tool_is_optional_and_does_not_force_general_question_into_research():
    transport = Transport()
    case = run([finish()], api=client(transport))
    assert case.result["status"] == "completed" and transport.calls == []
    names = {s["name"] for s in case.schemas[0]}
    assert "search_arxiv" in names and "run_multi_agent_research" in names
    assert not any(e["node_type"] == "Planner" for e in case.events)
    disabled = run([finish()])
    assert "search_arxiv" not in {s["name"] for s in disabled.schemas[0]}


def test_metadata_feedback_reaches_next_decision_and_has_no_evidence_citation():
    transport = Transport()
    case = run(
        [("search_arxiv", {"query": "ti:fixture"}), metadata_finish],
        api=client(transport),
    )
    assert case.result["status"] == "completed", case.result
    assert len(transport.calls) == 1
    observation = json.loads(case.calls[1][-1]["content"])
    assert (
        observation["tool_name"] == "search_arxiv"
        and observation["status"] == "success"
    )
    assert observation["payload"]["papers"][0]["paper_id"] == "arxiv:" + IDENTITY
    assert case.result["answer_first"]["citations"] == []
    assert any(
        e["node_type"] == "PaperSearch"
        and e["details"].get("tool_name") == "search_arxiv"
        for e in case.events
    )


def test_invalid_arguments_are_feedback_then_policy_submits_valid_call():
    transport = Transport()
    case = run(
        [
            ("search_arxiv", {"query": "ti:fixture", "paper_id": "wrong-field"}),
            ("search_arxiv", {"query": "ti:fixture"}),
            metadata_finish,
        ],
        api=client(transport),
    )
    assert case.result["status"] == "completed", case.result
    assert len(transport.calls) == 1
    assert (
        json.loads(case.calls[1][-1]["content"])["issue"]["code"] == "invalid_arguments"
    )


def test_external_only_paper_cannot_bypass_existing_local_research_handoff():
    identity = "arxiv:" + IDENTITY
    args = research_args([identity], source="retrieved")
    case = run(
        [
            ("search_arxiv", {"query": "ti:fixture"}),
            ("run_multi_agent_research", args),
            finish("insufficient", limitations=["No prepared paper evidence."]),
        ],
        api=client(),
    )
    assert not any(e["node_type"] == "Planner" for e in case.events)
    assert not case.result.get("handoffs")
    assert case.result["answer_first"]["citations"] == []


def test_exact_local_external_hit_still_allows_policy_chosen_multi_agent_path():
    case = run(
        [
            ("search_arxiv", {"query": "ti:fixture"}),
            (
                "run_multi_agent_research",
                research_args([PAPERS[0]], source="retrieved"),
            ),
            research_finish,
        ],
        api=client(Transport(feed(identity="2301.12597v1"))),
        question="Find and study relevant visual-language evidence.",
    )
    assert case.result["status"] == "completed", case.result
    assert any(e["node_type"] == "Planner" for e in case.events)
    assert len(case.result["handoffs"]) == 1
    assert case.result["handoffs"][0]["candidate_references"][0]["provider"] == "arxiv"
    assert case.result["answer_first"]["citations"]
    assert case.result["handoffs"][0]["retrieval_queries"] == ["ti:fixture"]


def test_existing_text_qa_remains_available_without_external_search():
    transport = Transport()

    def answer(messages):
        evidence = json.loads(messages[-1]["content"])["payload"]["evidence"]
        return finish("evidence", evidence_ids=[evidence[0]["evidence_id"]])

    case = run(
        [
            (
                "search_text",
                {
                    "query": "vision language interface",
                    "paper_ids": [PAPERS[0]],
                    "top_k": 1,
                },
            ),
            answer,
        ],
        api=client(transport),
        question="Explain BLIP-2 evidence.",
    )
    assert case.result["status"] == "completed", case.result
    assert not transport.calls and case.result["answer_first"]["citations"]


def test_bad_external_configuration_does_not_break_general_answer():
    case = run(
        [finish()],
        environment={
            "AGENT_ARXIV_ENABLED": "yes",
            "AGENT_ARXIV_TIMEOUT_SECONDS": "bad",
        },
    )
    assert case.result["status"] == "completed"
    assert "search_arxiv" in {s["name"] for s in case.schemas[0]}


def test_external_search_uses_existing_shared_tool_budget():
    transport = Transport()
    case = run(
        [
            ("search_arxiv", {"query": "ti:fixture"}),
            ("search_arxiv", {"query": "ti:another"}),
            finish("metadata", paper_ids=["arxiv:" + IDENTITY]),
        ],
        api=client(transport),
        max_tools=1,
    )
    assert len(transport.calls) == 1
    feedback = json.loads(case.calls[2][-1]["content"])
    assert feedback["status"] == "error"
    assert feedback["payload"]["issue_code"] == "tool_budget_exhausted"


def test_external_timeout_is_feedback_after_one_bounded_transport_retry():
    transport = Transport(error=TimeoutError("private timeout"))
    case = run(
        [
            ("search_arxiv", {"query": "ti:fixture"}),
            finish(
                "insufficient",
                limitations=["External service timed out; no prepared evidence."],
            ),
        ],
        api=client(transport),
    )
    observation = json.loads(case.calls[1][-1]["content"])
    assert observation["status"] == "error"
    assert observation["payload"]["error_category"] == "timeout"
    assert len(transport.calls) == 2
    assert not any(e["node_type"] == "Planner" for e in case.events)
    assert case.result["answer_first"]["citations"] == []


def test_policy_can_reformulate_after_exhausted_transport_retries():
    from app.papers.arxiv import HTTPResponse

    class RecoveringTransport(Transport):
        def __call__(self, url, timeout):
            self.calls.append((url, timeout))
            return (
                HTTPResponse(503, b"", "application/xml")
                if len(self.calls) <= 2
                else feed()
            )

    transport = RecoveringTransport()
    case = run(
        [
            ("search_arxiv", {"query": "ti:first"}),
            ("search_arxiv", {"query": "ti:revised"}),
            metadata_finish,
        ],
        api=client(transport),
    )
    first = json.loads(case.calls[1][-1]["content"])
    assert first["status"] == "error" and first["payload"]["execution"]["attempts"] == 2
    assert len(transport.calls) == 3 and "revised" in transport.calls[-1][0]
    assert case.result["status"] == "completed"
    assert not any(e["node_type"] == "Planner" for e in case.events)


def test_same_failed_action_cannot_create_unbounded_transport_requests():
    transport = Transport(error=ConnectionError("offline network failure"))
    case = run(
        [
            ("search_arxiv", {"query": "ti:same"}),
            ("search_arxiv", {"query": "ti:same"}),
            finish("insufficient", limitations=["Network unavailable."]),
        ],
        api=client(transport),
    )
    assert len(transport.calls) <= 4 and len(case.calls) <= 3
    assert case.result["status"] != "completed"
    assert not any(e["node_type"] == "Planner" for e in case.events)
