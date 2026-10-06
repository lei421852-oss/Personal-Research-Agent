import json
from pathlib import Path

import pytest

from app.agent.loop import BoundedToolAgent
from app.agent.recovery import SearchActionFingerprint
from app.api.mapping import map_agent_result
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from eval.metrics.cost import cost_metrics
from scripts.demo_day5 import (
    ControlledRetrieval,
    ControlledVLM,
    action,
    last_observation,
)

ROOT = Path(__file__).resolve().parents[3]
CLIP = "clip-2103.00020v1"
BLIP = "blip-2201.12086v2"
FLAMINGO = "flamingo-2204.14198v1"


@pytest.fixture
def corpus():
    return load_paper_corpus(ROOT / "data/papers")


class CountingRetrieval(ControlledRetrieval):
    def __init__(self, corpus):
        super().__init__(corpus)
        self.calls = []

    def search_text(self, query, paper_ids, top_k):
        self.calls.append((query, paper_ids, top_k))
        return super().search_text(query, paper_ids, top_k)


def build(corpus, responses):
    retrieval = CountingRetrieval(corpus)
    executor = ToolExecutor(
        create_paper_registry(
            corpus, retrieval, analyzer=FigureAnalyzer(ControlledVLM())
        )
    )
    llm = ScriptedLLMClient(responses)
    return (
        BoundedToolAgent(llm, executor, EvidenceResolver(corpus)),
        llm,
        retrieval,
        executor,
    )


def context(messages):
    return json.loads(
        messages[0]["content"].split("Current decision context (data):\n")[1]
    )


def finish_observed(messages, tools):
    ids = [e["evidence_id"] for e in context(messages)["observed_evidence"]]
    return action(
        "finish",
        "finish",
        answer="Scripted grounded answer; behavior verification only.",
        evidence_ids=ids[:1],
        limitations=[],
    )


@pytest.mark.parametrize(
    "second_query",
    [
        "  CLIP,   training objective image text ALIGNMENT! ",
        "CLIP training objective text image alignment",
    ],
)
def test_duplicate_feedback_no_execution_and_api_compatibility(corpus, second_query):
    def finish(messages, tools):
        obs = last_observation(messages)
        assert obs["issue"]["code"] == "redundant_action"
        assert obs["payload"]["failure"]["executed"] is False
        assert obs["payload"]["existing_evidence_ids"]
        data = context(messages)
        assert data["budget_used"] == 1 and data["budget_remaining"] == 5
        assert data["decision_steps_used"] == 2
        assert len(data["previous_actions"]) == 2
        assert data["previous_failures"][-1]["issue"]["code"] == "redundant_action"
        assert data["observed_evidence"][0]["summary"]
        return finish_observed(messages, tools)

    agent, llm, retrieval, executor = build(
        corpus,
        [
            action(
                "first",
                "search_text",
                query="CLIP training objective image text alignment",
                paper_ids=[CLIP],
            ),
            action("duplicate", "search_text", query=second_query, paper_ids=[CLIP]),
            finish,
        ],
    )
    result = agent.run("What objective does CLIP use?")
    assert result.status == "completed"
    assert (result.tool_calls, result.model_calls, len(retrieval.calls)) == (1, 3, 1)
    assert len(executor.traces) == 1 and len(result.state.trace) == 2
    metrics = cost_metrics(1, [], [], result.state.trace)
    assert metrics["tool_calls"] == 1
    dto = map_agent_result(result, corpus, vlm_calls=0, retrieval_mode="test")
    assert len(dto.trace) == 3
    assert dto.trace[1].failure["issue_code"] == "redundant_action"
    assert "not executed" in dto.trace[1].failure["message"]
    assert dto.metrics.tool_calls == 1
    assert all(
        m["content"] is None for m in result.state.messages if m["role"] == "assistant"
    )
    assert "reasoning_content" not in json.dumps(llm.calls)


def test_similarity_scope_k_and_information_change():
    def fp(query, scope=None, k=3):
        return SearchActionFingerprint.from_arguments(
            {"query": query, "paper_ids": scope, "top_k": k}
        )

    first = fp("CLIP training objective aligns image text", [CLIP, BLIP])
    assert fp(
        "CLIP training objective aligns image text loss", [BLIP, CLIP, CLIP]
    ).duplicates(first)
    assert not fp("CLIP training objective aligns image text", [CLIP]).duplicates(first)
    assert not fp("CLIP training objective aligns image text", None).duplicates(first)
    assert not fp(
        "CLIP training objective aligns image text", [BLIP, CLIP], 5
    ).duplicates(first)
    assert not fp("image encoder architecture", [CLIP, BLIP]).duplicates(first)
    assert not fp(
        "CLIP training objective aligns image text not", [BLIP, CLIP]
    ).duplicates(first)
    assert not fp(
        "CLIP training objective aligns image text figure3", [BLIP, CLIP]
    ).duplicates(first)
    assert fp("same", []).paper_ids != fp("same", None).paper_ids
    assert SearchActionFingerprint.from_arguments({"query": "x", "top_k": True}) is None


def test_necessary_page_context_after_success_is_allowed(corpus):
    def page(messages, tools):
        assert last_observation(messages)["status"] == "success"
        assert context(messages)["observed_evidence"]
        return action("context", "read_page", paper_id=FLAMINGO, page=11)

    agent, _, retrieval, _ = build(
        corpus,
        [
            action(
                "first",
                "search_text",
                query="fixed visual tokens",
                paper_ids=[FLAMINGO],
                top_k=1,
            ),
            page,
            finish_observed,
        ],
    )
    result = agent.run("Why does Flamingo use the Perceiver Resampler?")
    assert result.status == "completed" and result.tool_calls == 2
    assert len(retrieval.calls) == 1
    assert [t.tool_name for t in result.state.trace] == ["search_text", "read_page"]


def test_redundant_feedback_can_choose_different_search(corpus):
    def revised(messages, tools):
        assert last_observation(messages)["issue"]["code"] == "redundant_action"
        return action(
            "new",
            "search_text",
            query="reduce cross attention complexity",
            paper_ids=[FLAMINGO],
        )

    agent, _, retrieval, _ = build(
        corpus,
        [
            action("first", "search_text", query="fixed tokens", paper_ids=[FLAMINGO]),
            action("dup", "search_text", query="FIXED TOKENS!", paper_ids=[FLAMINGO]),
            revised,
            finish_observed,
        ],
    )
    result = agent.run("Why resample visual features?")
    assert result.status == "completed" and result.tool_calls == 2
    assert len(retrieval.calls) == 2


def test_invalid_paper_recovery_is_model_choice(corpus):
    def correct(messages, tools):
        obs = last_observation(messages)
        assert obs["status"] == "error"
        assert obs["issue"]["code"] == "invalid_arguments"
        assert obs["payload"]["domain_issue_code"] == "unknown_paper"
        assert BLIP in obs["payload"]["valid_paper_ids"]
        return action(
            "correct",
            "search_text",
            query="BLIP understanding generation",
            paper_ids=[BLIP],
        )

    agent, _, retrieval, _ = build(
        corpus,
        [
            action(
                "bad",
                "search_text",
                query="BLIP understanding generation",
                paper_ids=["BLIP"],
            ),
            correct,
            finish_observed,
        ],
    )
    result = agent.run("Why does BLIP use understanding and generation?")
    assert result.status == "completed"
    assert result.state.trace[0].arguments["paper_ids"] == ["BLIP"]
    assert retrieval.calls[0][1] == [BLIP]
    assert len(retrieval.calls) == 1


@pytest.mark.parametrize("recover", [True, False])
def test_empty_result_keeps_semantics_and_model_can_recover_or_finish(corpus, recover):
    def next_step(messages, tools):
        obs = last_observation(messages)
        assert obs["status"] == "empty" and obs["issue"]["code"] == "empty_result"
        assert "completed successfully" in obs["payload"]["instruction"]
        if recover:
            return action(
                "retry", "search_text", query="contrastive objective", paper_ids=[CLIP]
            )
        return action(
            "finish",
            "finish",
            answer="Prepared evidence is insufficient.",
            evidence_ids=[],
            limitations=["No evidence returned."],
        )

    responses = [
        action("empty", "search_text", query="unmatched first query"),
        next_step,
    ]
    if recover:
        responses.append(finish_observed)
    agent, _, retrieval, _ = build(corpus, responses)
    result = agent.run("Question")
    assert result.status == ("completed" if recover else "insufficient_evidence")
    assert len(retrieval.calls) == (2 if recover else 1)


def test_duplicate_requests_exhaust_decisions_not_tools(corpus):
    agent, _, retrieval, _ = build(
        corpus,
        [
            action(
                str(i), "search_text", query="contrastive objective", paper_ids=[CLIP]
            )
            for i in range(7)
        ],
    )
    result = agent.run("CLIP objective")
    assert result.status == "step_exhaustion"
    assert result.model_calls == 7 and result.tool_calls == 1
    assert len(retrieval.calls) == 1
    assert len(result.state.trace) == 7


def test_figure_analysis_can_finish_without_forced_page(corpus):
    def analyze(messages, tools):
        figure = last_observation(messages)["payload"]["figure"]
        return action(
            "analyze",
            "analyze_figure",
            question="Verify superiority premise",
            figure_evidence_id=figure["evidence_id"],
            context_evidence_ids=[],
        )

    def finish(messages, tools):
        analysis = last_observation(messages)["payload"]["analysis"]
        assert "does not establish superior performance" in analysis["interpretation"]
        return action(
            "finish",
            "finish",
            answer=analysis["interpretation"],
            evidence_ids=analysis["evidence_used"],
            limitations=analysis["limitations"],
        )

    agent, _, retrieval, _ = build(
        corpus,
        [
            action("figure", "find_figure", paper_id=BLIP, figure_id="Figure 3"),
            analyze,
            finish,
        ],
    )
    result = agent.run("Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。")
    assert result.status == "completed" and result.tool_calls == 2
    assert not retrieval.calls
    assert [t.tool_name for t in result.state.trace] == [
        "find_figure",
        "analyze_figure",
    ]


def test_catalog_and_scoped_search_are_model_choices(corpus):
    def choose_scope(messages, tools):
        catalog = context(messages)["available_papers"]
        assert catalog == [
            {"name": metadata.paper_key.upper(), "paper_id": metadata.paper_id}
            for metadata in corpus.metadata
        ]
        schema = next(t for t in tools if t["name"] == "search_text")
        assert "MUST restrict paper_ids" in schema["description"]
        assert (
            "MUST supply"
            in schema["input_schema"]["properties"]["paper_ids"]["description"]
        )
        scope = next(p["paper_id"] for p in catalog if p["name"] == "CLIP")
        return action(
            "scoped", "search_text", query="contrastive objective", paper_ids=[scope]
        )

    agent, _, retrieval, _ = build(corpus, [choose_scope, finish_observed])
    result = agent.run("What training objective does CLIP use?")
    assert result.status == "completed"
    assert retrieval.calls[0][1] == [CLIP]
    assert all(c.paper_id == CLIP for c in result.citations)


def test_missing_scope_is_not_silently_rewritten(corpus):
    agent, _, retrieval, _ = build(
        corpus,
        [
            action("unscoped", "search_text", query="CLIP training objective"),
            finish_observed,
        ],
    )
    result = agent.run("What training objective does CLIP use?")
    assert result.status == "completed"
    assert retrieval.calls[0][1] is None
    assert result.state.trace[0].arguments.get("paper_ids") is None


def test_reported_queries_are_not_duplicates_and_scope_recovery_is_allowed(corpus):
    first_query = "CLIP training objective align images and text contrastive"
    revised_query = "CLIP contrastive loss image-text alignment Radford"
    first = SearchActionFingerprint.from_arguments({"query": first_query})
    second = SearchActionFingerprint.from_arguments({"query": revised_query})
    assert not second.duplicates(first)
    agent, _, retrieval, _ = build(
        corpus,
        [
            action("first", "search_text", query=first_query),
            action("scoped", "search_text", query=revised_query, paper_ids=[CLIP]),
            finish_observed,
        ],
    )
    result = agent.run("What objective does CLIP use?")
    assert result.status == "completed" and result.tool_calls == 2
    assert [call[1] for call in retrieval.calls] == [None, [CLIP]]
    assert all(t.status == "success" for t in result.state.trace)
