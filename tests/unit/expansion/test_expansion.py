import json
from pathlib import Path

import pytest

from app.agent.loop import BoundedToolAgent
from app.api.mapping import map_agent_result
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.papers.candidates import union_candidates
from app.papers.catalog import load_paper_catalog
from app.papers.expansion import QueryExpander, QueryExpansionError, validate_expansion
from app.papers.models import RetrievedPaper
from app.tools.executor import ToolExecutor
from app.tools.expansion import ExpandSearchQueryTool
from app.tools.paper_registry import create_paper_registry
from app.tools.registry import ToolRegistry
from eval.adaptive_paper_discovery import comparison_metrics
from scripts.demo_day5 import ControlledRetrieval, action, last_observation

ROOT = Path(__file__).resolve().parents[3]
QUERY = "Find research on multimodal learning approaches"


def submit(queries, original=QUERY):
    return action(
        "generated",
        "submit_expanded_queries",
        original_query=original,
        expanded_queries=queries,
    )


class PaperSearch:
    def __init__(self, catalog):
        self.catalog = catalog
        self.calls = []

    def search(self, query, top_k=20):
        self.calls.append(query)
        ids = ["clip-2103.00020v1", "blip-2201.12086v2"]
        return [
            RetrievedPaper(paper=self.catalog.resolve(i), score=1, rank=r + 1)
            for r, i in enumerate(ids)
        ]


def build(policy_responses, expansion_responses):
    corpus = load_paper_corpus(ROOT / "data/papers")
    catalog = load_paper_catalog()
    search = PaperSearch(catalog)
    generator_llm = ScriptedLLMClient(expansion_responses)
    expander = QueryExpander(generator_llm)
    ex = ToolExecutor(
        create_paper_registry(
            corpus,
            ControlledRetrieval(corpus),
            paper_retriever=search,
            paper_catalog=catalog,
            query_expander=expander,
        )
    )
    llm = ScriptedLLMClient(policy_responses)
    return (
        BoundedToolAgent(llm, ex, EvidenceResolver(corpus, paper_catalog=catalog)),
        llm,
        expander,
        search,
        ex,
        corpus,
    )


def finish(messages, tools):
    return action(
        "finish",
        "finish",
        answer="Discovery collected metadata only.",
        evidence_ids=[],
        limitations=["Metadata is not scientific evidence."],
    )


@pytest.mark.parametrize(
    "queries", [[""], [" "], ["!!!"], [QUERY], ["View A", "VIEW A!"], ["a", "b", "c"]]
)
def test_invalid_blank_duplicate_and_excess_queries(queries):
    with pytest.raises(QueryExpansionError):
        validate_expansion(
            QUERY, {"original_query": QUERY, "expanded_queries": queries}
        )


def test_original_is_retained_and_not_replaced():
    result = validate_expansion(
        QUERY,
        {
            "original_query": QUERY,
            "expanded_queries": [
                "Multimodal representation learning",
                "Visual and language learning tasks",
            ],
        },
    )
    assert result.original_query == QUERY and len(result.expanded_queries) == 2
    with pytest.raises(QueryExpansionError):
        validate_expansion(
            QUERY, {"original_query": "Different intent", "expanded_queries": ["a"]}
        )


def test_generation_tool_never_retrieves_and_has_no_gold_context():
    llm = ScriptedLLMClient([submit(["Multimodal representation learning"])])
    registry = ToolRegistry()
    registry.register(ExpandSearchQueryTool(QueryExpander(llm)))
    result = ToolExecutor(registry).execute("expand_search_query", {"query": QUERY})
    assert result.status == "success" and result.payload["original_query"] == QUERY
    assert len(llm.calls) == 1
    assert llm.calls[0]["messages"][-1]["content"] == QUERY
    assert len(llm.calls[0]["tools"]) == 1
    request = json.dumps(llm.calls[0])
    assert "paper_id" not in request and "relevant_paper_ids" not in request
    assert "answer-specific" in request
    assert result.result_count == 0  # 查询生成不是返回证据。


def test_invalid_tool_input_and_generation_failure():
    llm = ScriptedLLMClient([submit([""])])
    expander = QueryExpander(llm)
    registry = ToolRegistry()
    registry.register(ExpandSearchQueryTool(expander))
    ex = ToolExecutor(registry)
    assert (
        ex.execute("expand_search_query", {"query": " "}).issue.code
        == "invalid_arguments"
    )
    assert expander.calls == 0
    result = ex.execute("expand_search_query", {"query": QUERY})
    assert result.status == "error" and result.issue.code == "invalid_expansion"
    assert expander.calls == 1


def test_second_expansion_is_budget_rejected_without_execution():
    def after(messages, tools):
        obs = last_observation(messages)
        assert obs["issue"]["code"] == "expansion_budget_exceeded"
        assert obs["payload"]["failure"]["executed"] is False
        return finish(messages, tools)

    agent, _llm, expander, _search, ex, corpus = build(
        [
            action("orig", "search_papers", query=QUERY),
            action("expand", "expand_search_query", query=QUERY),
            action("again", "expand_search_query", query=QUERY),
            after,
        ],
        [submit(["Multimodal representation learning"])],
    )
    result = agent.run(QUERY)
    assert expander.calls == 1 and result.tool_calls == 2 and result.model_calls == 4
    assert len(ex.traces) == 2
    dto = map_agent_result(result, corpus, vlm_calls=0, retrieval_mode="test")
    assert dto.trace[2].failure["issue_code"] == "expansion_budget_exceeded"
    assert "not executed" in dto.trace[2].failure["message"]


def test_failed_generation_still_consumes_expansion_budget():
    agent, _, expander, _, _, _ = build(
        [
            action("first", "expand_search_query", query=QUERY),
            action("second", "expand_search_query", query=QUERY),
            finish,
        ],
        [submit([QUERY])],
    )
    result = agent.run(QUERY)
    assert expander.calls == 1
    assert [o.issue.code for o in result.state.observations if o.status == "error"] == [
        "invalid_expansion",
        "expansion_budget_exceeded",
    ]


def test_expanded_search_union_dedup_and_query_sources():
    variants = [
        "Multimodal representation learning",
        "Visual and language learning tasks",
    ]
    agent, llm, expander, search, _, _ = build(
        [
            action("orig", "search_papers", query=QUERY),
            action("expand", "expand_search_query", query=QUERY),
            action("one", "search_papers", query=variants[0]),
            action("two", "search_papers", query=variants[1]),
            finish,
        ],
        [submit(variants)],
    )
    result = agent.run(QUERY)
    assert search.calls == [QUERY, *variants] and expander.calls == 1
    assert result.tool_calls == 4
    merged = union_candidates(result.state.observations)
    assert len(merged) == 2 and all(len(p["retrieved_by"]) == 3 for p in merged)
    assert [s["query"] for s in merged[0]["retrieved_by"]] == [QUERY, *variants]
    context = json.loads(
        llm.calls[-1]["messages"][0]["content"].split(
            "Current decision context (data):\n"
        )[1]
    )
    assert len(context["discovered_papers"]) == 2
    assert context["query_expansion"]["calls_remaining"] == 0
    assert result.state.observed_evidence_ids == set() and not result.citations


def test_unapproved_and_duplicate_searches_are_not_executed():
    agent, _, _, search, ex, _ = build(
        [
            action("orig", "search_papers", query=QUERY),
            action("repeat", "search_papers", query=QUERY.upper() + "!"),
            action(
                "unapproved",
                "search_papers",
                query="Unintroduced method specific query",
            ),
            finish,
        ],
        [],
    )
    result = agent.run(QUERY)
    assert search.calls == [QUERY]
    assert result.tool_calls == 1 and len(ex.traces) == 1
    assert result.state.observations[1].issue.code == "redundant_action"
    assert result.state.observations[2].issue.code == "invalid_arguments"


@pytest.mark.parametrize(
    "paper_id,name", [("clip-2103.00020v1", "CLIP"), ("blip-2201.12086v2", "BLIP")]
)
def test_named_known_items_do_not_automatically_expand(paper_id, name):
    def finish_evidence(messages, tools):
        item = last_observation(messages)["payload"]["evidence"][0]
        return action(
            "finish",
            "finish",
            answer="Scripted evidence answer",
            evidence_ids=[item["evidence_id"]],
            limitations=[],
        )

    agent, _, expander, search, _, _ = build(
        [
            action("text", "search_text", query="objective", paper_ids=[paper_id]),
            finish_evidence,
        ],
        [],
    )
    result = agent.run(name + " training objective")
    assert result.status == "completed"
    assert expander.calls == 0 and search.calls == []


def test_controller_does_not_classify_request_by_paper_keywords():
    query = "CLIP training objective"
    agent, _, expander, _, _, _ = build(
        [action("expand", "expand_search_query", query=query), finish],
        [submit(["CLIP image text training objective"], original=query)],
    )
    agent.run(query)
    assert expander.calls == 1  # Known-item 避免扩展属于 Policy，不是关键词路由。


def test_union_coverage_is_not_global_topk_recall():
    from app.tools.observations import ToolObservation

    first = [{"paper_id": str(i), "score": 1} for i in range(10)]
    extra = [{"paper_id": "target", "score": 1}]
    observations = [
        ToolObservation(
            status="success",
            tool_name="search_papers",
            summary="first",
            payload={"query": "original", "top_k": 10, "papers": first},
        ),
        ToolObservation(
            status="success",
            tool_name="search_papers",
            summary="second",
            payload={"query": "expanded", "top_k": 10, "papers": extra},
        ),
    ]
    result = comparison_metrics(
        [p["paper_id"] for p in first], observations, ["target"]
    )
    assert result["recall_at_5"]["adaptive_fixed_total_k"] == 0
    assert result["recall_at_5"]["adaptive_union_per_query_k"] == 1
    assert result["union_candidate_count_at_5"] == 6
    assert result["new_relevant_paper_ids"] == ["target"]


def test_empty_list_can_abstain_but_blank_query_cannot():
    llm = ScriptedLLMClient([submit([])])
    expander = QueryExpander(llm)
    registry = ToolRegistry()
    registry.register(ExpandSearchQueryTool(expander))
    observation = ToolExecutor(registry).execute(
        "expand_search_query", {"query": QUERY}
    )
    assert observation.status == "empty" and observation.issue.code == "empty_result"
    assert observation.payload["original_query"] == QUERY
    assert observation.payload["expanded_queries"] == []


def test_api_counts_generation_calls_separately_from_decision_budget():
    from app.api.runtime import AgentAPIRuntime
    from scripts.demo_day5 import ControlledVLM

    corpus = load_paper_corpus(ROOT / "data/papers")
    catalog = load_paper_catalog()
    llm = ScriptedLLMClient(
        [
            action("orig", "search_papers", query=QUERY),
            action("expand", "expand_search_query", query=QUERY),
            submit(["Multimodal representation learning"]),
            finish,
        ]
    )
    runtime = AgentAPIRuntime(
        corpus,
        ControlledRetrieval(corpus),
        lambda stack: (llm, ControlledVLM()),
        paper_retriever=PaperSearch(catalog),
        paper_catalog=catalog,
    )
    response = runtime.run(QUERY)
    assert response.metrics.model_calls == 4  # 三轮决策 + 一次生成，决策预算仍独立。
    assert response.metrics.tool_calls == 2
    assert [t.tool_name for t in response.trace] == [
        "search_papers",
        "expand_search_query",
        "finish",
    ]


def test_summary_uses_serialized_query_sources_and_reports_target_hits():
    from eval.adaptive_paper_discovery import summarize_report

    rows = []
    for case_id, target in [
        ("discovery-03", "instructblip-2305.06500v1"),
        ("discovery-08", "ofa-2202.03052v1"),
    ]:
        observations = [
            {
                "tool_name": "search_papers",
                "status": "success",
                "payload": {"query": "new view", "papers": [{"paper_id": target}]},
            }
        ]
        rows.append(
            {
                "case_id": case_id,
                "query": "original",
                "observations": observations,
                "expansion_attempted": True,
                "expanded_queries": ["new view"],
                "baseline": {
                    "paper_ids": [],
                    "model_calls": 0,
                    "embedding_calls": 1,
                    "latency_ms": 1,
                },
                "adaptive": {"model_calls": 3, "embedding_calls": 2, "latency_ms": 10},
                "recall_at_5": {
                    "baseline": 0,
                    "adaptive_fixed_total_k": 1,
                    "adaptive_union_per_query_k": 1,
                },
                "recall_at_10": {
                    "baseline": 0,
                    "adaptive_fixed_total_k": 1,
                    "adaptive_union_per_query_k": 1,
                },
                "additional_llm_calls": 3,
                "additional_embedding_calls": 1,
                "additional_latency_ms": 9,
            }
        )
    report = summarize_report({"cases": rows})
    assert report["status"] == "completed" and report["expansion_rate"] == 1
    assert report["cost_totals"]["additional_embedding_calls"] == 2
    assert all(c["retrieved_by_new_query"] for c in report["targeted_checks"].values())
    assert report["targeted_checks"]["discovery-08"]["sources"] == [
        {"query": "new view", "rank": 1}
    ]


def test_repeated_expansion_requests_exhaust_decisions_without_more_generation():
    agent, _, expander, search, ex, _ = build(
        [action(str(i), "expand_search_query", query=QUERY) for i in range(7)],
        [submit(["Multimodal representation learning"])],
    )
    result = agent.run(QUERY)
    assert result.status == "step_exhaustion"
    assert result.model_calls == 7 and result.tool_calls == 1
    assert expander.calls == 1 and search.calls == [] and len(ex.traces) == 1


def test_public_trace_preserves_query_to_papers_without_fake_evidence_ids():
    agent, _, _, _, _, corpus = build(
        [action("orig", "search_papers", query=QUERY), finish], []
    )
    result = agent.run(QUERY)
    dto = map_agent_result(result, corpus, vlm_calls=0, retrieval_mode="test")
    assert dto.trace[0].arguments["query"] == QUERY
    assert dto.trace[0].retrieved_paper_ids == [
        "clip-2103.00020v1",
        "blip-2201.12086v2",
    ]
    assert dto.trace[0].evidence_ids == []
    assert result.state.trace[0].retrieved_paper_ids == dto.trace[0].retrieved_paper_ids
