import json
from pathlib import Path

import pytest

from app.agent.loop import BoundedToolAgent
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse
from app.tools.errors import DependencyFailure
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from scripts.demo_day5 import (
    ControlledRetrieval,
    ControlledVLM,
    action,
    last_observation,
    run_cases,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def corpus():
    return load_paper_corpus(ROOT / "data/papers")


def build(corpus, responses, retrieval=None, budget=6):
    llm = ScriptedLLMClient(responses)
    executor = ToolExecutor(
        create_paper_registry(
            corpus,
            retrieval or ControlledRetrieval(corpus),
            analyzer=FigureAnalyzer(ControlledVLM()),
        )
    )
    return (
        BoundedToolAgent(
            llm, executor, EvidenceResolver(corpus), max_tool_calls=budget
        ),
        llm,
        executor,
    )


def test_four_complete_routes(corpus):
    reports = run_cases(corpus)
    assert [r["result"]["status"] for r in reports] == [
        "completed",
        "completed",
        "completed",
        "step_exhaustion",
    ]
    assert [r["result"]["tool_calls"] for r in reports] == [1, 2, 2, 6]
    assert [r["result"]["model_calls"] for r in reports] == [2, 3, 3, 7]
    assert [t["tool_name"] for t in reports[1]["result"]["state"]["trace"]] == [
        "find_figure",
        "analyze_figure",
    ]
    changed = reports[2]
    assert changed["result"]["state"]["trace"][0]["status"] == "empty"
    assert (
        changed["result"]["state"]["trace"][0]["arguments"]["query"]
        != changed["result"]["state"]["trace"][1]["arguments"]["query"]
    )
    assert changed["model_requests"][1]["messages"][-1]["role"] == "tool"
    assert (
        json.loads(changed["model_requests"][1]["messages"][-1]["content"])["status"]
        == "empty"
    )
    assert reports[-1]["model_requests"][-1]["tools"][0]["name"] == "finish"
    assert len(reports[-1]["model_requests"][-1]["tools"]) == 1
    assert len(reports[-1]["result"]["state"]["model_actions"]) == 7
    assert reports[-1]["result"]["state"]["model_actions"][-1]["tool_name"] == "read_page"


def test_unobserved_corpus_id_cannot_finish(corpus):
    unobserved = corpus.text_evidence[0].evidence_id

    def correct(messages, tools):
        assert last_observation(messages)["status"] == "error"
        return action(
            "correct",
            "finish",
            answer="Evidence insufficient",
            evidence_ids=[],
            limitations=["No evidence was observed."],
        )

    agent, _, _ = build(
        corpus,
        [
            action(
                "invent",
                "finish",
                answer="Claim",
                evidence_ids=[unobserved],
                limitations=[],
            ),
            correct,
        ],
    )
    result = agent.run("Question")
    assert result.status == "insufficient_evidence" and result.citations == []
    assert result.tool_calls == 0 and result.model_calls == 2


def test_unknown_finish_id_rejected(corpus):
    agent, _, _ = build(
        corpus,
        [
            action(
                str(i),
                "finish",
                answer=f"Claim {i}",
                evidence_ids=["ev_fake"],
                limitations=[],
            )
            for i in range(7)
        ],
    )
    result = agent.run("Question")
    assert result.status == "step_exhaustion" and result.answer is None
    assert result.tool_calls == 0 and result.model_calls == 7


@pytest.mark.parametrize(
    "args",
    [
        {"answer": "x", "evidence_ids": [], "limitations": []},
        {"answer": " ", "evidence_ids": [], "limitations": ["Insufficient"]},
        {"answer": "x", "evidence_ids": [], "limitations": [], "path": "/tmp/x"},
    ],
)
def test_invalid_finish_is_feedback(corpus, args):
    agent, llm, _ = build(
        corpus,
        [
            action("bad", "finish", **args),
            action(
                "good",
                "finish",
                answer="Insufficient evidence",
                evidence_ids=[],
                limitations=["Evidence unavailable"],
            ),
        ],
    )
    result = agent.run("Question")
    assert result.status == "insufficient_evidence"
    assert (
        last_observation(llm.calls[1]["messages"])["issue"]["code"]
        == "invalid_arguments"
    )


def test_identical_empty_calls_stop(corpus):
    calls = [
        action(
            str(i),
            "search_text",
            query="unmatched first query",
            paper_ids=["flamingo"],
            top_k=1,
        )
        for i in range(7)
    ]
    agent, llm, _ = build(corpus, calls)
    result = agent.run("Question")
    assert (
        result.status == "repeated_failure"
        and result.tool_calls == 2
        and result.model_calls == 2
    )
    assert len(llm.calls) == 2


@pytest.mark.parametrize("failure", ["dependency", "invalid"])
def test_error_feedback_and_strategy_change(corpus, failure):
    class FailingRetrieval:
        def search_text(self, *args):
            raise DependencyFailure("secret stack /home/private/api")

    first = action(
        "first",
        "search_text",
        query="q",
        paper_ids=["blip"],
        top_k=1 if failure == "dependency" else 0,
    )

    def after(messages, tools):
        observation = last_observation(messages)
        assert observation["status"] == "error"
        assert observation["issue"]["retryable"] == (failure == "dependency")
        assert "secret" not in messages[-1]["content"]
        return action(
            "stop",
            "finish",
            answer="Evidence unavailable",
            evidence_ids=[],
            limitations=["Tool failed"],
        )

    agent, _, _ = build(corpus, [first, after], FailingRetrieval())
    assert agent.run("Question").status == "insufficient_evidence"


def test_sixth_observation_can_be_finished(corpus):
    responses = [
        action(str(i), "read_page", paper_id="flamingo", page=p)
        for i, p in enumerate([4, 9, 10, 11, 4, 9])
    ]

    def final(messages, tools):
        evidence = last_observation(messages)["payload"]["evidence"][0]
        assert [t["name"] for t in tools] == ["finish"]
        return action(
            "finish",
            "finish",
            answer=evidence["text"],
            evidence_ids=[evidence["evidence_id"]],
            limitations=[],
        )

    agent, _, _ = build(corpus, [*responses, final])
    result = agent.run("Question")
    assert (
        result.status == "completed"
        and result.tool_calls == 6
        and result.model_calls == 7
    )


def test_full_figure_context_route(corpus):
    def page(messages, tools):
        figure = last_observation(messages)["payload"]["figure"]
        return action(
            "page", "read_page", paper_id=figure["paper_id"], page=figure["page"]
        )

    def analyze(messages, tools):
        obs = last_observation(messages)
        figure = obs["payload"]["figures"][0]
        return action(
            "analyze",
            "analyze_figure",
            question="Explain",
            figure_evidence_id=figure["evidence_id"],
            context_evidence_ids=[e["evidence_id"] for e in obs["payload"]["evidence"]],
        )

    def finish(messages, tools):
        obs = last_observation(messages)["payload"]["analysis"]
        return action(
            "finish",
            "finish",
            answer=obs["interpretation"],
            evidence_ids=obs["evidence_used"],
            limitations=obs["limitations"],
        )

    agent, _, _ = build(
        corpus,
        [
            action("find", "find_figure", paper_id="blip", figure_id="Figure 3"),
            page,
            analyze,
            finish,
        ],
    )
    result = agent.run("Explain")
    assert result.status == "completed" and result.tool_calls == 3
    assert result.state.observations[2].result_count > 1


@pytest.mark.parametrize("budget", [0, 7, True, 1.5])
def test_budget_is_strict(corpus, budget):
    with pytest.raises(ValueError):
        build(corpus, [], budget=budget)


def test_no_tool_text_is_not_finish(corpus):
    agent, _, _ = build(corpus, [LLMResponse(content="Unsupported final answer")])
    result = agent.run("Question")
    assert result.status == "protocol_failure" and result.answer is None


def test_multiple_tool_calls_are_not_partially_executed(corpus):
    first = action("a", "read_page", paper_id="blip", page=4).tool_calls[0]
    second = action("b", "read_page", paper_id="blip", page=3).tool_calls[0]
    agent, _, executor = build(corpus, [LLMResponse(tool_calls=[first, second])])
    assert agent.run("Question").status == "protocol_failure"
    assert executor.traces == ()


def test_no_reasoning_content_stored(corpus):
    call = action(
        "x",
        "finish",
        answer="Insufficient",
        evidence_ids=[],
        limitations=["No evidence"],
    )
    call.content = "HIDDEN REASONING SHOULD NOT BE RECORDED"
    agent, _, _ = build(corpus, [call])
    result = agent.run("Question")
    assert "HIDDEN REASONING" not in result.model_dump_json()


def test_model_failure_is_safe(corpus):
    agent, _, _ = build(corpus, [])
    result = agent.run("Question")
    assert result.status == "model_failure" and result.answer is None


def test_adjust_then_finish_still_insufficient(corpus):
    class EmptyRetrieval:
        def search_text(self, *args):
            return []

    def adjust(messages, tools):
        assert last_observation(messages)["status"] == "empty"
        return action(
            "adjust", "search_text", query="new wording", paper_ids=["clip"], top_k=1
        )

    def finish(messages, tools):
        assert last_observation(messages)["status"] == "empty"
        return action(
            "finish",
            "finish",
            answer="Insufficient evidence to answer.",
            evidence_ids=[],
            limitations=["Both searches returned no prepared evidence."],
        )

    agent, _, _ = build(
        corpus,
        [
            action(
                "first",
                "search_text",
                query="first wording",
                paper_ids=["clip"],
                top_k=1,
            ),
            adjust,
            finish,
        ],
        EmptyRetrieval(),
    )
    result = agent.run("Question")
    assert (
        result.status == "insufficient_evidence"
        and result.tool_calls == 2
        and result.model_calls == 3
    )


def test_repeated_error_stops(corpus):
    calls = [
        action(str(i), "search_text", query="q", paper_ids=["clip"], top_k=0)
        for i in range(7)
    ]
    agent, _, _ = build(corpus, calls)
    result = agent.run("Question")
    assert result.status == "repeated_failure" and result.tool_calls == 2
    assert all(o.issue.code == "invalid_arguments" for o in result.state.observations)


def test_repeated_call_ids_rejected(corpus):
    calls = [action("same", "read_page", paper_id="blip", page=p) for p in [3, 4]]
    agent, _, _ = build(corpus, calls)
    result = agent.run("Question")
    assert result.status == "protocol_failure" and result.tool_calls == 1


def test_agent_reuse_does_not_inherit_observed_ids(corpus):
    def finish(messages, tools):
        item = last_observation(messages)["payload"]["evidence"][0]
        return action(
            "finish",
            "finish",
            answer=item["text"],
            evidence_ids=[item["evidence_id"]],
            limitations=[],
        )

    agent, _, _ = build(
        corpus,
        [
            action("search", "search_text", query="q", paper_ids=["clip"], top_k=1),
            finish,
            action(
                "next",
                "finish",
                answer="Unsupported",
                evidence_ids=[corpus.text_evidence[0].evidence_id],
                limitations=[],
            ),
            action(
                "stop",
                "finish",
                answer="Insufficient",
                evidence_ids=[],
                limitations=["No current-run evidence."],
            ),
        ],
    )
    assert agent.run("First").status == "completed"
    second = agent.run("Second")
    assert second.status == "insufficient_evidence" and second.tool_calls == 0
    assert second.state.observed_evidence_ids == set()


def test_identical_invalid_finish_stops(corpus):
    responses = [
        action(
            str(i), "finish", answer="Claim", evidence_ids=["ev_fake"], limitations=[]
        )
        for i in range(7)
    ]
    agent, _, _ = build(corpus, responses)
    result = agent.run("Question")
    assert (
        result.status == "repeated_failure"
        and result.model_calls == 2
        and result.tool_calls == 0
    )


def test_invalid_model_response_stops_safely(corpus):
    agent, _, _ = build(corpus, [])

    class InvalidModel:
        def complete(self, **kwargs):
            return {"content": "unexpected shape"}

    agent.llm = InvalidModel()
    result = agent.run("Question")
    assert result.status == "protocol_failure" and result.tool_calls == 0
