"""真实领域链+Scripted策略验证主入口契约；不作为真实模型能力评测。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent.finish import FinishValidationError
from app.evidence.resolver import EvidenceResolver
from app.llm.models import LLMResponse, ToolCall
from app.papers.catalog import load_paper_catalog
from app.papers.models import RetrievedPaper
from app.research.react_budget import ResearchBudgetView, UnifiedRunBudget
from app.research.react_policy import (
    MainFinishAction,
    MainFinishInput,
    MainResearchAgent,
)
from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.research_handoff import ResearchContext, ResearchToolInput
from app.research.research_tool import MultiAgentResearchTool
from app.research.retry import DependencyCallError
from app.research.run_manager import MainAgentRunRequest, ResearchRunManager
from app.research.run_replay import ReplayRepository
from app.research.workbench_dependencies import ROOT
from tests.unit.research.test_workbench_runtime import (
    FakeDependencies as BaseDependencies,
)
from tests.unit.research.test_workbench_runtime import clients as child_clients

PAPERS = ["blip-2-2301.12597v1", "flamingo-2204.14198v1"]
CATALOG = load_paper_catalog()


class Dependencies(BaseDependencies):
    def __init__(self):
        super().__init__()
        self.resource_calls = 0

    def prepare_resources(self):
        self.resource_calls += 1
        return super().prepare_resources()

    def health(self):
        return {
            "main_agent_ready": True,
            "real_research_ready": False,
            "language_adapter_ready": True,
            "replay_ready": True,
        }

    def papers(self):
        return [
            {"paper_id": p.paper_id, "title": p.title} for p in self.corpus.metadata
        ]


class PaperSearch:
    def __init__(self, ids=PAPERS):
        self.ids, self.calls = ids, []

    def search(self, query, top_k=20):
        self.calls.append((query, top_k))
        return [
            RetrievedPaper(paper=CATALOG.resolve(i), score=1.0, rank=r)
            for r, i in enumerate(self.ids[:top_k], 1)
        ]


def response(name, arguments, identity):
    return LLMResponse(
        tool_calls=[ToolCall(id=f"main-{identity}", name=name, arguments=arguments)]
    )


def research_args(ids=PAPERS, source="named"):
    return {
        "research_goal": "Compare BLIP-2 and Flamingo design evidence.",
        "selected_paper_ids": ids,
        "selection_source": source,
        "selection_rationale": {i: "Relevant to the original question." for i in ids},
    }


def finish(kind="general", **extra):
    return (
        "finish",
        {
            "answer": "A direct answer to the user's question.",
            "answer_kind": kind,
            **extra,
        },
    )


def factory(dependencies, steps, *, fail_translation=False):
    base, vision, created = child_clients(dependencies.corpus)
    observed, calls = [], []
    index = 0

    class Client(base):
        def complete(self, *, messages, tools):
            nonlocal index
            names = {t["name"] for t in tools}
            if "run_multi_agent_research" in names or (names == {"finish"}):
                calls.append(deepcopy(messages))
                current = steps[index]
                index += 1
                if callable(current):
                    current = current(messages)
                return response(*current, index)
            if "translate_report_fields" in names:
                if fail_translation:
                    raise ValueError("test translation failure")
                fields = json.loads(messages[-1]["content"])["fields"]
                return response(
                    "translate_report_fields",
                    {"translations": {k: "中文展示：" + v for k, v in fields.items()}},
                    "translation",
                )
            if "translate_research_question" in names:
                value = json.loads(messages[-1]["content"])
                return response(
                    "translate_research_question",
                    {
                        "english_question": "Compare BLIP-2 and Flamingo design evidence.",
                        "preserved_terms": value["preserved_terms"],
                    },
                    "query",
                )
            if "submit_expanded_queries" in names:
                query = messages[-1]["content"]
                return response(
                    "submit_expanded_queries",
                    {
                        "original_query": query,
                        "expanded_queries": [query + " retrieval perspective"],
                    },
                    "expanded",
                )
            if "resolve_requirement" in names:
                content = json.loads(messages[-1]["content"])
                if not any(
                    e.paper_id == content["paper_id"]
                    for e in dependencies.corpus.text_evidence
                ):
                    return response(
                        "resolve_requirement",
                        {
                            "requirement_id": content["unresolved_requirements"][0][
                                "requirement_id"
                            ],
                            "status": "insufficient_evidence",
                            "limitations": ["Prepared body evidence unavailable."],
                        },
                        "missing",
                    )
            return super().complete(messages=messages, tools=tools)

    return Client, vision, created, calls, observed


def run(
    steps,
    *,
    question="Explain evidence-grounded research.",
    ids=(),
    maximum=200,
    fail_translation=False,
    paper_search=None,
):
    dependencies = Dependencies()
    llm, vision, created, calls, events = factory(
        dependencies, steps, fail_translation=fail_translation
    )
    search = paper_search or PaperSearch()
    runtime = ReActWorkbenchRuntime(
        dependencies,
        llm_factory=llm,
        vision_factory=vision,
        paper_retriever=search,
        paper_catalog=CATALOG,
    )
    request = MainAgentRunRequest(
        question=question, paper_ids=list(ids), max_model_calls=maximum
    )
    result = asyncio.run(runtime.run(request, lambda **event: events.append(event)))
    return SimpleNamespace(
        result=result,
        runtime=runtime,
        deps=dependencies,
        events=events,
        calls=calls,
        clients=created,
        search=search,
    )


def research_finish(messages):
    value = json.loads(messages[-1]["content"])
    claims = value["payload"]["verified_claim_references"]
    return finish(
        "research",
        claim_ids=[c["claim_id"] for c in claims],
        evidence_ids=list(dict.fromkeys(e for c in claims for e in c["evidence_ids"])),
    )


def test_direct_answer_does_not_create_research_roles_or_load_embedding():
    case = run([finish()])
    assert case.result["status"] == "completed", case.result
    answer = case.result["answer_first"]
    assert answer["answer_status"] == "answered"
    assert answer["execution_mode"] == "direct"
    assert answer["metrics"]["tool_calls"] == 0
    assert answer["metrics"]["model_calls"] == 2  # 主决策与一次字段翻译
    assert case.deps.resource_calls == 0
    assert not case.search.calls
    assert not {"Planner", "Worker", "Reviewer", "Synthesis"}.intersection(
        e["node_type"] for e in case.events
    )
    assert all(c.closed for c in case.clients)
    assert answer["chinese_report"]["direct_answer"].startswith("中文")


def test_discovery_can_finish_metadata_without_forced_multi_agent():
    query = "Find related papers."
    case = run(
        [
            ("search_papers", {"query": query, "top_k": 2}),
            finish("metadata", paper_ids=PAPERS),
        ],
        question=query,
    )
    assert case.result["status"] == "completed", case.result
    assert case.result["answer_first"]["execution_mode"] == "tools"
    assert len(case.search.calls) == 1
    assert case.deps.resource_calls == 0
    assert any(m["role"] == "tool" for m in case.calls[1])
    assert case.result["answer_first"]["citations"] == []
    assert not any(e["node_type"] == "Worker" for e in case.events)


def test_tool_can_upgrade_to_real_domain_multi_agent_chain_then_return_to_main():
    query = "Compare BLIP-2 and Flamingo design evidence."
    case = run(
        [
            ("search_papers", {"query": query, "top_k": 2}),
            ("run_multi_agent_research", research_args(source="retrieved")),
            research_finish,
        ],
        question=query,
    )
    assert case.result["status"] == "completed", case.result
    answer = case.result["answer_first"]
    assert answer["execution_mode"] == "multi_agent_research"
    assert len(answer["key_findings"]) == 2
    assert len(answer["citations"]) == 2
    assert answer["detailed_report"]["metrics"]["verified_requirements"] == 2
    assert answer["metrics"]["model_calls"] == 12
    assert answer["metrics"]["tool_calls"] == 4
    assert case.result["handoffs"][0]["candidate_references"]
    assert case.result["handoffs"][0]["retrieval_queries"] == [query]
    assert "report_reference" in case.calls[2][-1]["content"]
    assert answer["answer_review_status"] == "not_independently_reviewed"
    assert any(e["node_type"] == "Reviewer" for e in case.events)


def test_named_research_needs_no_paper_search_and_accepts_one_paper():
    case = run(
        [("run_multi_agent_research", research_args([PAPERS[0]])), research_finish],
        question="Explain BLIP-2 design evidence.",
    )
    assert case.result["status"] == "completed", case.result
    assert not case.search.calls
    assert len(case.result["answer_first"]["citations"]) == 1
    assert case.result["handoffs"][0]["selected_paper_ids"] == [PAPERS[0]]


def test_tools_evidence_answer_observes_scoped_retrieval():
    query = "Explain CLIP training."
    paper = "clip-2103.00020v1"

    def end(messages):
        item = json.loads(messages[-1]["content"])["payload"]["evidence"][0]
        return finish("evidence", evidence_ids=[item["evidence_id"]])

    case = run(
        [("search_text", {"query": query, "paper_ids": [paper], "top_k": 1}), end],
        question=query,
    )
    assert case.result["status"] == "completed", case.result
    assert case.result["answer_first"]["citations"][0]["paper_id"] == paper
    assert not any(e["node_type"] == "Worker" for e in case.events)


def test_chinese_input_and_output_share_total_budget():
    case = run(
        [("run_multi_agent_research", research_args()), research_finish],
        question="比较 BLIP-2 和 Flamingo 的设计证据。",
    )
    assert case.result["status"] == "completed", case.result
    assert case.result["answer_first"]["original_question"].startswith("比较")
    assert (
        case.result["answer_first"]["metrics"]["role_model_calls"]["query_language"]
        == 1
    )
    assert case.result["answer_first"]["metrics"]["model_calls"] == 12


def test_translation_failure_keeps_valid_original_answer():
    case = run([finish()], fail_translation=True)
    assert case.result["status"] == "completed"
    assert case.result["answer_first"]["direct_answer"]
    assert case.result["answer_first"]["language"]["status"] == "failed"
    assert case.result["answer_first"]["chinese_report"] is None


@pytest.mark.parametrize(
    "source,ids",
    [
        ("retrieved", PAPERS),
        ("named", ["ofa-2202.03052v1"]),
        ("explicit_scope", PAPERS),
    ],
)
def test_invalid_handoff_is_rejected_without_starting_child(source, ids):
    case = run(
        [
            ("run_multi_agent_research", research_args(ids, source)),
            finish("insufficient", limitations=["The handoff is invalid."]),
        ]
    )
    assert not case.result["handoffs"]
    assert case.deps.resource_calls == 0
    assert "invalid_handoff" in case.calls[1][-1]["content"]


def test_child_budget_does_not_get_independent_allowance():
    case = run(
        [
            ("run_multi_agent_research", research_args()),
            finish("insufficient", limitations=["Not enough budget."]),
        ],
        question="Compare BLIP-2 and Flamingo design.",
        maximum=4,
    )
    assert not case.result["handoffs"]
    assert case.result["model_calls_observed"] <= 4
    assert "research_budget_unavailable" in case.calls[1][-1]["content"]


def test_second_research_is_rejected_without_executing_another_pipeline():
    def after_error(messages):
        assert "research_call_limit" in messages[-1]["content"]
        earlier = next(
            m
            for m in messages
            if m.get("role") == "tool" and "verified_claim_references" in m["content"]
        )
        return research_finish([earlier])

    case = run(
        [
            ("run_multi_agent_research", research_args()),
            ("run_multi_agent_research", research_args([PAPERS[0]])),
            after_error,
        ],
        question="Compare BLIP-2 and Flamingo design.",
    )
    assert len(case.result["handoffs"]) == 1
    assert case.result["answer_first"]["metrics"]["research_calls"] == 1


def test_shared_budget_reserves_one_final_main_decision():
    budget = UnifiedRunBudget(3)
    view = ResearchBudgetView(budget)
    view.reserve(role="worker")
    view.reserve(role="reviewer")
    with pytest.raises(DependencyCallError):
        view.reserve(role="synthesis")
    budget.reserve(role="main_agent")
    assert budget.used == 3


def test_finished_default_loop_implementation_is_reused():
    from app.agent.loop import BoundedToolAgent

    assert MainResearchAgent.run is BoundedToolAgent.run


@pytest.mark.parametrize("kind", ["evidence", "research", "metadata"])
def test_finish_rejects_unobserved_or_fabricated_references(kind):
    deps = Dependencies()
    ctx = ResearchContext("Question", "Question", CATALOG, set())
    resolver = EvidenceResolver(deps.corpus, paper_catalog=CATALOG)
    final = MainFinishAction(resolver, ctx)
    data = finish(
        kind, evidence_ids=["fake-id"], claim_ids=["fake-claim"], paper_ids=PAPERS
    )[1]
    with pytest.raises(FinishValidationError):
        final.finalize(data, set())


def test_schema_rejects_duplicate_targets_and_extra_fields():
    args = research_args([PAPERS[0], PAPERS[0]])
    with pytest.raises(ValueError):
        ResearchToolInput.model_validate(args)
    with pytest.raises(ValueError):
        MainFinishInput.model_validate(finish()[1] | {"hidden_reasoning": "x"})


def test_index_catalog_is_read_only_and_matches_all_records():
    from app.research.discovery_resources import load_index_catalog

    catalog, binding = load_index_catalog(ROOT)
    assert binding["record_count"] == 25
    assert len(catalog.documents) == 25
    assert (
        sum(
            bool(p.tasks or p.methods or p.capabilities)
            for p in catalog.documents.values()
        )
        == 24
    )


def test_empty_discovery_is_business_insufficiency_not_crash():
    question = "Find something absent."
    case = run(
        [
            ("search_papers", {"query": question, "top_k": 2}),
            finish("insufficient", limitations=["No matching metadata was returned."]),
        ],
        question=question,
        paper_search=PaperSearch([]),
    )
    assert case.result["status"] == "completed"
    assert case.result["answer_first"]["answer_status"] == "insufficient"
    assert case.result["answer_first"]["citations"] == []
    assert '"status":"empty"' in case.calls[1][-1]["content"]


def test_metadata_only_paper_scoped_body_lookup_cannot_create_citation():
    query = "Explain OFA architecture."
    identity = "ofa-2202.03052v1"
    case = run(
        [
            ("search_papers", {"query": query, "top_k": 1}),
            ("search_text", {"query": query, "paper_ids": [identity], "top_k": 1}),
            finish(
                "insufficient", limitations=["Prepared body evidence is unavailable."]
            ),
        ],
        question=query,
        paper_search=PaperSearch([identity]),
    )
    assert case.result["answer_first"]["citations"] == []
    assert '"status":"empty"' in case.calls[2][-1]["content"]
    assert identity in case.calls[2][-1]["content"]
    assert case.deps.resource_calls == 0


def test_tool_failure_feedback_does_not_become_general_answer():
    query = "Find related papers."

    class BrokenSearch(PaperSearch):
        def search(self, *args, **kwargs):
            raise ValueError("private dependency failure")

    case = run(
        [
            ("search_papers", {"query": query, "top_k": 1}),
            finish("insufficient", limitations=["Search dependency failed."]),
        ],
        question=query,
        paper_search=BrokenSearch(),
    )
    assert case.result["status"] == "failed"
    assert case.result["answer_first"]["answer_status"] == "failed"
    assert "private dependency failure" not in json.dumps(case.result)


def test_explicit_scope_cannot_be_silently_replaced():
    query = "Read explicit scope."
    case = run(
        [
            ("search_text", {"query": query, "paper_ids": [PAPERS[1]], "top_k": 1}),
            finish("insufficient", limitations=["Requested scope was invalid."]),
        ],
        question=query,
        ids=[PAPERS[0]],
    )
    assert case.deps.resource_calls == 0
    assert case.result["answer_first"]["citations"] == []
    assert "未静默修改范围" in case.calls[1][-1]["content"]


def test_main_budget_exhaustion_is_bounded_and_keeps_events():
    query = "Find related papers."
    case = run(
        [
            ("search_papers", {"query": query, "top_k": 1}),
            finish("metadata", paper_ids=[PAPERS[0]]),
        ],
        question=query,
        maximum=1,
    )
    assert case.result["model_calls_observed"] == 1
    assert case.result["status"] == "failed"
    assert case.result["main_agent"]["status"] == "model_failure"
    assert any(e["node_type"] == "PaperSearch" for e in case.events)


def test_tool_budget_stops_search_without_real_retriever_execution():
    async def scenario():
        deps = Dependencies()
        search = PaperSearch()
        llm, vision, _, _, events = factory(
            deps,
            [
                ("search_papers", {"query": "One query", "top_k": 1}),
                ("search_papers", {"query": "Another query", "top_k": 1}),
                finish("insufficient", limitations=["Tool budget exhausted."]),
            ],
        )
        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=llm,
            vision_factory=vision,
            paper_retriever=search,
            paper_catalog=CATALOG,
            max_tools=1,
        )
        result = await runtime.run(
            MainAgentRunRequest(question="One query"),
            lambda **event: events.append(event),
        )
        return result, search

    result, search = asyncio.run(scenario())
    assert len(search.calls) == 1
    assert result["answer_first"]["metrics"]["tool_calls"] == 1


def test_main_agent_rejects_batch_without_executing_first_action():
    async def scenario():
        deps = Dependencies()
        search = PaperSearch()
        base, vision, _created = child_clients(deps.corpus)

        class Batch(base):
            def complete(self, *, messages, tools):
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id=str(i),
                            name="search_papers",
                            arguments={"query": "Batch", "top_k": 1},
                        )
                        for i in range(2)
                    ]
                )

        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=Batch,
            vision_factory=vision,
            paper_retriever=search,
            paper_catalog=CATALOG,
        )
        result = await runtime.run(
            MainAgentRunRequest(question="Batch"), lambda **event: None
        )
        return result, search

    result, search = asyncio.run(scenario())
    assert result["main_agent"]["status"] == "protocol_failure"
    assert not search.calls
    assert result["answer_first"]["metrics"]["tool_calls"] == 0


def test_child_has_no_recursive_research_tool():
    case = run(
        [("run_multi_agent_research", research_args()), research_finish],
        question="Compare BLIP-2 and Flamingo.",
    )
    assert case.result["answer_first"]["metrics"]["research_calls"] == 1
    assert case.result["coverage_worker_enabled"] is False
    assert case.result["answer_first"]["detailed_report"]["verified_claims"]


def test_manager_delivers_incremental_main_events_and_answer_first_dto():
    async def scenario():
        deps = Dependencies()
        llm, vision, _, _, _ = factory(deps, [finish()])
        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=llm,
            vision_factory=vision,
            paper_retriever=PaperSearch(),
            paper_catalog=CATALOG,
        )
        manager = ResearchRunManager(runtime, deps, ReplayRepository(ROOT))
        status = await manager.create(
            MainAgentRunRequest(question="Explain a concept.")
        )
        identity = status["run_id"]
        await manager._runs[identity].task
        result = manager.status(identity)
        events = manager.events(identity)["events"]
        report = manager.report(identity)
        await manager.close()
        return result, events, report

    status, events, report = asyncio.run(scenario())
    assert status["status"] == "completed"
    assert report["direct_answer"]
    assert any(e["node_type"] == "MainAgent" for e in events)
    assert not any(e["node_type"] == "Worker" for e in events)
    assert [e["event_id"] for e in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize("case_id", ["g2", "g2-1"])
def test_real_history_replay_keeps_original_claims_without_provider_calls(case_id):
    async def scenario():
        deps = Dependencies()
        runtime = ReActWorkbenchRuntime(
            deps, llm_factory=lambda **kw: pytest.fail("No provider on replay")
        )
        manager = ResearchRunManager(runtime, deps, ReplayRepository(ROOT))
        result = await manager.replay(case_id)
        report = manager.report(result["run_id"])
        await manager.close()
        return report

    report = asyncio.run(scenario())
    assert report["direct_answer"]
    assert report["execution_mode"] == "historical_replay"
    assert report["detailed_report"]["provenance"]["case_id"] == case_id
    assert report["chinese_report"]


def test_cancel_waits_for_sync_model_and_never_launches_later_actions():
    import threading

    async def scenario():
        deps = Dependencies()
        started = threading.Event()
        release = threading.Event()
        base, vision, created = child_clients(deps.corpus)

        class Blocked(base):
            def complete(self, *, messages, tools):
                started.set()
                release.wait(3)
                return response(*finish(), "blocked")

        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=Blocked,
            vision_factory=vision,
            paper_retriever=PaperSearch(),
            paper_catalog=CATALOG,
        )
        task = asyncio.create_task(
            runtime.run(MainAgentRunRequest(question="Explain."), lambda **event: None)
        )
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()  # 软取消仍须等待同步SDK收尾
        release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert all(c.closed for c in created)
        return error.value.evaluation_snapshot

    result = asyncio.run(scenario())
    assert result["status"] == "cancelled"
    assert result["model_calls_observed"] == 1
    assert result["answer_first"]["answer_status"] == "cancelled"


def test_query_expansion_is_optional_generated_then_separately_searched():
    query = "Find multimodal approaches."
    case = run(
        [
            ("search_papers", {"query": query, "top_k": 2}),
            ("expand_search_query", {"query": query}),
            ("search_papers", {"query": query + " retrieval perspective", "top_k": 2}),
            finish("metadata", paper_ids=PAPERS),
        ],
        question=query,
    )
    assert case.result["status"] == "completed", case.result
    assert len(case.search.calls) == 2
    assert (
        case.result["answer_first"]["metrics"]["role_model_calls"]["query_expansion"]
        == 1
    )
    assert case.result["answer_first"]["metrics"]["model_calls"] == 6
    assert not any(e["node_type"] == "Worker" for e in case.events)


def test_unprepared_research_target_is_preserved_and_reported_missing():
    identity = "ofa-2202.03052v1"
    args = research_args([identity])
    args["research_goal"] = "Explain OFA design evidence."
    case = run(
        [
            ("run_multi_agent_research", args),
            finish("insufficient", limitations=["No prepared OFA body evidence."]),
        ],
        question="Explain OFA design evidence.",
    )
    assert case.result["handoffs"][0]["paper_availability"] == {identity: False}
    report = case.result["answer_first"]["detailed_report"]
    assert report["paper_ids"] == [identity]
    assert not report["verified_claims"]
    assert report["missing_requirements"]
    assert case.result["answer_first"]["citations"] == []


def test_main_answer_validation_failure_keeps_authoritative_report():
    def fake_citation(messages):
        return finish(
            "research", claim_ids=["fake-claim"], evidence_ids=["fake-evidence"]
        )

    case = run(
        [
            ("run_multi_agent_research", research_args()),
            fake_citation,
            finish("insufficient", limitations=["Could not form a grounded summary."]),
        ],
        question="Compare BLIP-2 and Flamingo.",
    )
    assert "finish" in case.calls[2][-1]["content"]
    assert len(case.result["answer_first"]["detailed_report"]["verified_claims"]) == 2
    assert case.result["answer_first"]["citations"] == []
    assert case.result["answer_first"]["answer_status"] == "insufficient"


def test_failed_partial_child_keeps_verified_claim_references_for_main_decision():
    deps = Dependencies()
    query = "Compare BLIP-2 and Flamingo."
    good = run(
        [("run_multi_agent_research", research_args()), research_finish], question=query
    )
    # 来自领域模型的真实结构，用独立副本模拟取消后已保存的部分产物。
    from app.research.workbench_runtime import WorkbenchRuntime

    base, vision, _ = child_clients(deps.corpus)
    child = asyncio.run(
        WorkbenchRuntime(deps, llm_factory=base, vision_factory=vision).run(
            SimpleNamespace(question=query, paper_ids=PAPERS, max_model_calls=200),
            lambda **e: None,
        )
    )
    child["research"]["status"] = "execution_failed"
    ctx = ResearchContext(query, query, CATALOG, set(PAPERS))
    budget = UnifiedRunBudget(200)
    tool = MultiAgentResearchTool(ctx, budget, lambda op: op(), lambda handoff: child)
    obs = tool.execute(ResearchToolInput.model_validate(research_args()))
    assert obs.status == "error"
    assert obs.result_count == 0
    assert len(obs.payload["verified_claim_references"]) == 2
    assert not obs.payload.get("evidence")
    assert (
        good.result["answer_first"]["answer_review_status"]
        == "not_independently_reviewed"
    )


def test_cancel_during_chinese_display_keeps_completed_main_answer():
    import threading

    async def scenario():
        deps = Dependencies()
        started = threading.Event()
        release = threading.Event()
        normal, vision, _created, _, _ = factory(deps, [finish()])

        class Blocked(normal):
            def complete(self, *, messages, tools):
                if any(t["name"] == "translate_report_fields" for t in tools):
                    started.set()
                    release.wait(3)
                return super().complete(messages=messages, tools=tools)

        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=Blocked,
            vision_factory=vision,
            paper_catalog=CATALOG,
            paper_retriever=PaperSearch(),
        )
        task = asyncio.create_task(
            runtime.run(
                MainAgentRunRequest(question="Explain a concept."), lambda **e: None
            )
        )
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        return error.value.evaluation_snapshot

    result = asyncio.run(scenario())
    assert result["status"] == "cancelled"
    assert result["answer_first"]["answer_status"] == "answered"
    assert (
        result["original_answer"]["direct_answer"]
        == result["answer_first"]["direct_answer"]
    )
    assert result["answer_first"]["language"]["status"] == "failed"


def test_unsafe_fields_are_removed_from_public_answer_and_events():
    from app.research.run_events import sanitize_public

    public = sanitize_public(
        {
            "direct_answer": "<script>window.attack=1</script>",
            "reasoning_content": "private",
            "api_key": "sk-secret",
            "path": "/home/lei/private/file",
        }
    )
    assert "reasoning_content" not in public
    assert "api_key" not in public
    assert "/home/lei/private" not in json.dumps(public)
    assert (
        "<script>" in public["direct_answer"]
    )  # 界面用textContent安全呈现，不将其执行


def test_explicit_scope_rejects_outside_figure_before_vlm_or_budget():
    deps = Dependencies()
    figure = next(
        f for f in deps.corpus.figure_evidence if f.paper_id == "blip-2201.12086v2"
    )
    case = run(
        [
            (
                "analyze_figure",
                {
                    "question": "Explain this figure.",
                    "figure_evidence_id": figure.evidence_id,
                    "context_evidence_ids": [],
                },
            ),
            finish(
                "insufficient", limitations=["Figure is outside the explicit scope."]
            ),
        ],
        question="Explain the scoped figure.",
        ids=["clip-2103.00020v1"],
    )
    assert case.result["vlm_calls_observed"] == 0
    assert case.result["answer_first"]["metrics"]["tool_calls"] == 0
    assert "未静默修改范围" in case.calls[1][-1]["content"]
