"""Fake 模型贯穿原研究链；验证适配连线，不作为中文语义或研究质量评测。"""

import asyncio
import json
import threading

import pytest

from app.research.paper_selection import PaperSelectionAdapter, PaperSelectionError
from app.research.query_language import query_terms
from app.research.run_manager import ResearchRunManager, ResearchRunRequest
from app.research.run_replay import ReplayRepository
from app.research.workbench_dependencies import ROOT
from app.research.workbench_runtime import WorkbenchRuntime
from scripts.evaluate_cross_paper import action
from tests.unit.research.test_workbench_runtime import (
    FakeDependencies as BaseDependencies,
)
from tests.unit.research.test_workbench_runtime import clients as base_clients

PAPERS = ["blip-2-2301.12597v1", "flamingo-2204.14198v1"]


class FakeDependencies(BaseDependencies):
    """复用真实 Prepared Corpus；模型、检索和资源仅用 Fake。"""

    def papers(self):
        return [
            {"paper_id": paper.paper_id, "title": paper.title}
            for paper in self.corpus.metadata
            if any(
                item.paper_id == paper.paper_id for item in self.corpus.text_evidence
            )
        ]

    def health(self):
        return {
            "ui_ready": True,
            "replay_ready": True,
            "real_research_ready": True,
            "language_adapter_ready": True,
        }


def clients(
    corpus,
    *,
    reject_initial=False,
    selected_papers=None,
    invalid_query=False,
    translation_failure=False,
    selection_failure=False,
    language_started=None,
    language_release=None,
):
    """供浏览器工程检查复用：所有角色按工具 Schema 分发，无真实网络。"""
    base, vision, created = base_clients(corpus, reject_initial=reject_initial)

    class Client(base):
        def complete(self, *, messages, tools):
            names = {tool["name"] for tool in tools}
            payload = json.loads(messages[-1]["content"])
            if "translate_research_question" in names:
                self.calls.append({"status": "fake_language"})
                terms = query_terms(payload["original_question"])
                return action(
                    "translate_research_question",
                    {
                        "english_question": ""
                        if invalid_query
                        else "Compare the prepared evidence for "
                        + ", ".join(terms)
                        + ".",
                        "preserved_terms": terms,
                    },
                )
            if "select_research_papers" in names:
                self.calls.append({"status": "fake_selection"})
                if selection_failure:
                    raise ConnectionError(
                        "Fake Provider unavailable /private/path sk-private"
                    )
                identities = PAPERS if selected_papers is None else selected_papers
                return action(
                    "select_research_papers",
                    {
                        "paper_ids": identities,
                        "reasons": {
                            identity: "The user's comparison concerns this prepared paper."
                            for identity in identities
                        },
                    },
                )
            if "translate_report_fields" in names:
                self.calls.append({"status": "fake_translation"})
                if language_started:
                    language_started.set()
                if language_release:
                    language_release.wait(timeout=5)
                if translation_failure:
                    return action("translate_report_fields", {"translations": {}})
                # 仅供身份与结构回归：不声称这个可重复占位翻译具有语义质量。
                return action(
                    "translate_report_fields",
                    {
                        "translations": {
                            key: "中文展示：" + value
                            for key, value in payload["fields"].items()
                        }
                    },
                )
            return super().complete(messages=messages, tools=tools)

    return Client, vision, created


def execute(request, **fake_options):
    dependency = FakeDependencies()
    llm, vision, created = clients(dependency.corpus, **fake_options)
    events = []
    result = asyncio.run(
        WorkbenchRuntime(dependency, llm_factory=llm, vision_factory=vision).run(
            request, lambda **event: events.append(event)
        )
    )
    return result, events, created


def fake_runtime(*, reject_initial=False, report_failure=False):
    """浏览器测试唯一装配入口，仍执行原领域链，只替换基础设施。"""
    dependency = FakeDependencies()
    llm, vision, created = clients(
        dependency.corpus,
        reject_initial=reject_initial,
        translation_failure=report_failure,
    )
    return (
        WorkbenchRuntime(dependency, llm_factory=llm, vision_factory=vision),
        dependency,
        created,
    )


def test_zero_selection_chinese_enters_actual_planner_worker_reviewer_synthesis():
    request = ResearchRunRequest(question="比较 BLIP-2 与 Flamingo 的设计。")
    result, events, created = execute(request)
    assert result["paper_ids"] == PAPERS
    assert result["question"] == request.question
    assert result["research_question"] != request.question
    assert result["research"]["status"] == "completed"
    assert result["research"]["worker_classes_observed"] == ["PaperResearchWorker"] * 2
    assert not result["research"]["coverage_worker_enabled"]
    assert (
        result["research"]["outcome"]["report"]["metrics"]["verified_requirements"] == 2
    )
    assert result["report_language"]["status"] == "translated"
    assert result["model_calls_observed"] == 11
    assert all(client.closed for client in created)
    assert {event["node_type"] for event in events} >= {
        "QueryLanguage",
        "PaperSelection",
        "Planner",
        "Worker",
        "Reviewer",
        "Synthesis",
        "ReportLanguage",
    }


def test_explicit_english_scope_skips_query_translation_and_selection():
    result, events, _ = execute(
        ResearchRunRequest(question="Compare the designs.", paper_ids=PAPERS)
    )
    assert not any(
        event["node_type"] in {"QueryLanguage", "PaperSelection"} for event in events
    )
    assert result["query_language"]["model_calls"] == 0
    assert result["model_calls_observed"] == 9


def test_auto_selection_is_provider_result_not_fixed_default():
    chosen = ["clip-2103.00020v1", "blip-2201.12086v2"]
    result, _, _ = execute(
        ResearchRunRequest(question="Compare prepared designs."), selected_papers=chosen
    )
    assert result["paper_ids"] == chosen
    assert result["preparation"]["plan"]["paper_ids"] == chosen


@pytest.mark.parametrize(
    "chosen", [["ofa-2202.03052v1", PAPERS[0]], [PAPERS[0]], [PAPERS[0], PAPERS[0]]]
)
def test_invalid_catalog_only_single_or_duplicate_selection_never_starts_planner(
    chosen,
):
    result, events, _ = execute(
        ResearchRunRequest(question="Discover research papers."), selected_papers=chosen
    )
    assert result["research"]["issue_type"] == "paper_selection_failed"
    assert not any(event["node_type"] == "Planner" for event in events)
    assert not result["retrievals"]


def test_no_matching_papers_has_explicit_empty_result_without_fallback():
    result, events, _ = execute(
        ResearchRunRequest(question="Compare unavailable studies."), selected_papers=[]
    )
    assert result["research"]["issue_type"] == "no_matching_prepared_papers"
    assert result["paper_selection"]["status"] == "empty"
    assert result["model_calls_observed"] == 1
    assert not any(event["node_type"] == "Planner" for event in events)


def test_query_translation_failure_stops_before_selection_and_planner():
    result, events, _ = execute(
        ResearchRunRequest(question="比较 BLIP-2 和 Flamingo。"), invalid_query=True
    )
    assert result["research"]["issue_type"] == "query_language_failed"
    assert result["query_language"]["research_question"] is None
    assert not any(
        event["node_type"] in {"PaperSelection", "Planner"} for event in events
    )


def test_debug_original_mode_does_not_translate_chinese_question():
    request = ResearchRunRequest(
        question="比较两篇论文。", paper_ids=PAPERS, language_mode="original"
    )
    result, events, _ = execute(request)
    assert result["research_question"] == request.question
    assert not any(event["node_type"] == "QueryLanguage" for event in events)


def test_translation_failure_preserves_success_and_authoritative_report():
    result, _, _ = execute(
        ResearchRunRequest(question="Compare designs.", paper_ids=PAPERS),
        translation_failure=True,
    )
    assert result["research"]["status"] == "completed"
    assert result["report_language"]["status"] == "failed"
    assert result["research"]["outcome"]["report"]["verified_claims"]


def test_translation_budget_exhaustion_does_not_reclassify_completed_research():
    result, _, created = execute(
        ResearchRunRequest(
            question="Compare designs.", paper_ids=PAPERS, max_model_calls=8
        )
    )
    assert result["model_calls_observed"] == 8
    assert result["research"]["status"] == "completed"
    assert result["report_language"]["status"] == "failed"
    assert result["report_language"]["model_calls"] == 0
    assert sum(len(client.calls) for client in created) == 8


def test_language_and_selection_share_overall_budget_without_extra_calls():
    result, _, created = execute(
        ResearchRunRequest(question="比较 BLIP-2 与 Flamingo。", max_model_calls=1)
    )
    assert result["query_language"]["status"] == "adapted"
    assert result["research"]["status"] == "execution_failed"
    assert result["model_calls_observed"] == 1
    assert sum(len(client.calls) for client in created) == 1


def test_report_available_during_translation_and_cancel_keeps_original():
    async def scenario():
        dependency = FakeDependencies()
        started, release = threading.Event(), threading.Event()
        llm, vision, created = clients(
            dependency.corpus, language_started=started, language_release=release
        )
        manager = ResearchRunManager(
            WorkbenchRuntime(dependency, llm_factory=llm, vision_factory=vision),
            dependency,
            ReplayRepository(ROOT),
        )
        run = await manager.create(
            ResearchRunRequest(question="Compare designs.", paper_ids=PAPERS)
        )
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        await asyncio.sleep(0)
        original = manager.report(run["run_id"])
        assert original["verified_claims"]
        assert original["language"]["status"] == "pending"
        await manager.cancel(run["run_id"])
        release.set()
        await asyncio.gather(manager._runs[run["run_id"]].task, return_exceptions=True)
        preserved = manager.report(run["run_id"])
        assert preserved["verified_claims"] == original["verified_claims"]
        assert preserved["language"]["issue_code"] == "report_language_cancelled"
        assert manager.status(run["run_id"])["status"] == "cancelled"
        assert any(
            event["node_type"] == "ReportLanguage" and event["status"] == "failed"
            for event in manager.events(run["run_id"])["events"]
        )
        assert all(client.closed for client in created)
        await manager.close()

    asyncio.run(scenario())


def test_selection_adapter_schema_limits_to_real_prepared_ids():
    papers = [{"paper_id": identity, "title": identity} for identity in PAPERS]
    _, tools = PaperSelectionAdapter.prepare("Compare design.", papers)
    assert tools[0]["input_schema"]["properties"]["paper_ids"]["items"][
        "enum"
    ] == sorted(PAPERS)
    with pytest.raises(PaperSelectionError):
        PaperSelectionAdapter.prepare("Compare", papers[:1])


def test_new_selection_adapter_transient_failure_is_a_single_bounded_call():
    result, events, created = execute(
        ResearchRunRequest(question="Discover relevant prepared papers."),
        selection_failure=True,
    )
    assert result["paper_selection"]["status"] == "failed"
    assert result["paper_selection"]["model_calls"] == 1
    assert result["model_calls_observed"] == 1
    assert sum(len(client.calls) for client in created) == 1
    assert not any(event["node_type"] == "Planner" for event in events)
    assert "private" not in json.dumps(result)


def test_overall_timeout_during_translation_preserves_completed_research():
    async def scenario():
        dependency = FakeDependencies()
        started, release = threading.Event(), threading.Event()
        llm, vision, _ = clients(
            dependency.corpus, language_started=started, language_release=release
        )
        manager = ResearchRunManager(
            WorkbenchRuntime(dependency, llm_factory=llm, vision_factory=vision),
            dependency,
            ReplayRepository(ROOT),
            timeout_seconds=2,
        )
        run = await manager.create(
            ResearchRunRequest(question="Compare designs.", paper_ids=PAPERS)
        )
        await asyncio.to_thread(started.wait, 4)
        assert started.is_set()
        await asyncio.sleep(2.1)
        release.set()
        await manager._runs[run["run_id"]].task
        state = manager.status(run["run_id"])
        assert state["status"] == "completed"
        assert state["error"]["code"] == "report_language_timeout"
        assert manager.report(run["run_id"])["verified_claims"]
        assert state["report_language_status"] == "failed"
        await manager.close()

    asyncio.run(scenario())
