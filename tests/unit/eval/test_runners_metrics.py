import json

import pytest

from app.llm.fake import ScriptedLLMClient
from app.tools.errors import DependencyFailure
from eval.loader import load_dataset
from eval.metrics.retrieval import evidence_recall, recall_at_k
from eval.metrics.task_success import apply_review
from eval.runners.fixed_multimodal_rag import FixedMultimodalRAGRunner
from eval.runners.multimodal_agent import MultimodalAgentRunner
from eval.runners.resources import create_resources
from eval.runners.text_rag import TextRAGRunner
from eval.schemas import AnswerPoint, EvaluationReview
from eval.summarize_results import read_runs, summarize
from scripts.demo_day5 import ControlledVLM, action


@pytest.fixture
def resources():
    return create_resources("fake")


def case(category):
    return next(
        c
        for c in load_dataset("dev", require_approved=True).cases
        if c.category == category
    )


@pytest.mark.parametrize(
    "runner", [TextRAGRunner, FixedMultimodalRAGRunner, MultimodalAgentRunner]
)
def test_unified_contract_text_and_unreviewed_semantics(resources, runner):
    result = runner(resources).run(case("text_qa"))
    assert result.status == "completed" and result.answer
    assert result.citation_identity_valid
    assert result.task_success is None  # 数据集已批准，不代表生成答案也已通过审阅。
    assert result.citation_correctness is None
    assert result.task_success_source == "pending_review"
    assert result.model_calls >= 1 and result.vlm_calls == 0
    assert result.tool_calls == 1 and result.latency_ms >= 0
    assert "reasoning_content" not in result.model_dump_json()
    assert "/home/" not in result.model_dump_json()


def test_multimodal_paths_counts_and_figure_acquisition(resources):
    question = case("figure_qa")
    text = TextRAGRunner(resources).run(question)
    fixed = FixedMultimodalRAGRunner(resources).run(question)
    agent = MultimodalAgentRunner(resources).run(question)
    assert text.figure_recall_at_k == 0
    assert fixed.figure_recall_at_k == agent.figure_recall_at_k == 1
    assert (fixed.tool_calls, fixed.model_calls, fixed.vlm_calls) == (3, 0, 1)
    assert (agent.tool_calls, agent.model_calls, agent.vlm_calls) == (2, 3, 1)
    assert fixed.text_recall_at_k is None
    assert [s["tool_name"] for s in fixed.trace] == [
        "find_figure",
        "read_page",
        "analyze_figure",
    ]
    assert fixed.tool_latency_ms >= fixed.vlm_latency_ms


def test_gold_fields_never_enter_generation_or_filter(resources):
    original = case("text_qa")
    question = original.model_copy(
        update={
            "paper_ids": ["DO_NOT_USE_GOLD_SCOPE"],
            "answer_points": [
                AnswerPoint(
                    text="DO_NOT_LEAK_GOLD_ANSWER",
                    supporting_evidence_ids=original.relevant_evidence_ids,
                )
            ],
        }
    )
    requests = []
    base_factory = resources.client_factory

    def clients(stack):
        llm, vision = base_factory(stack)
        requests.append(llm.requests)
        return llm, vision

    resources.client_factory = clients
    result = TextRAGRunner(resources).run(question)
    serialized = json.dumps(requests)
    assert (
        "DO_NOT_LEAK_GOLD_ANSWER" not in serialized
        and "DO_NOT_USE_GOLD_SCOPE" not in serialized
    )
    assert result.trace[0]["arguments"]["paper_ids"] is None


def test_empty_is_zero_recall_dependency_failure_is_not_rank_score(resources):
    class Empty:
        def search_text(self, *args):
            return []

    resources.retrieval = Empty()
    empty = TextRAGRunner(resources).run(case("text_qa"))
    assert empty.text_recall_at_k == 0 and empty.status == "insufficient_evidence"

    class Broken:
        def search_text(self, *args):
            raise DependencyFailure("secret /home/private")

    resources.retrieval = Broken()
    broken = TextRAGRunner(resources).run(case("text_qa"))
    assert broken.status == "tool_failure" and broken.text_recall_at_k is None
    assert (
        broken.task_success is False
        and broken.model_calls == 0
        and broken.tool_calls == 1
    )
    assert "secret" not in broken.model_dump_json()


def test_invalid_agent_top_k_does_not_crash_metric(resources):
    def clients(stack):
        return ScriptedLLMClient(
            [
                action(
                    "invalid", "search_text", query="q", paper_ids=None, top_k="bad"
                ),
                action(
                    "stop",
                    "finish",
                    answer="Evidence unavailable",
                    evidence_ids=[],
                    limitations=["Arguments failed"],
                ),
            ]
        ), ControlledVLM()

    resources.client_factory = clients
    result = MultimodalAgentRunner(resources).run(case("text_qa"))
    assert result.status == "insufficient_evidence" and result.text_recall_at_k is None
    assert result.text_recall_k is None and result.tool_calls == 1


def test_recall_protocols_and_missing_gold():
    assert recall_at_k(["a", "b", "c"], ["b", "c"], 2) == 0.5
    assert evidence_recall(["a", "b"], ["b", "c"]) == 0.5
    assert recall_at_k([], ["a"], 3) == 0
    assert recall_at_k(["a"], [], 3) is None
    with pytest.raises(ValueError):
        recall_at_k([], [], True)


def test_review_requires_matching_run_and_semantic_identity_not_conflated(resources):
    result = TextRAGRunner(resources).run(case("text_qa"))
    review = EvaluationReview(
        run_id=result.run_id,
        reviewer="test reviewer",
        reviewed_at="2026-10-06T20:00:00+08:00",
        task_success=False,
        citation_correctness=0,
    )
    reviewed = apply_review(result, review)
    assert reviewed.task_success is False and reviewed.citation_correctness == 0
    assert reviewed.citation_identity_valid is True
    assert reviewed.task_success_source == "human_review"
    with pytest.raises(ValueError):
        apply_review(result, review.model_copy(update={"run_id": "another"}))


def test_summary_does_not_report_pending_semantics_as_accuracy(resources):
    result = TextRAGRunner(resources).run(case("text_qa"))
    summary = summarize([result])["systems"]["text_rag"]
    assert summary["task_success_rate"] is None and summary["pending_task_reviews"] == 1
    assert summary["citation_correctness"] is None
    assert summary["citation_identity_valid_rate_among_citing_runs"] == 1


def test_read_runs_rejects_duplicates(resources, tmp_path):
    result = TextRAGRunner(resources).run(case("text_qa"))
    path = tmp_path / "runs.jsonl"
    path.write_text(result.model_dump_json() + "\n" + result.model_dump_json() + "\n")
    with pytest.raises(ValueError, match="Duplicate run_id"):
        read_runs(path)


def test_hallucinated_finish_is_rejected_without_fabricated_answer(resources):
    def clients(stack):
        return ScriptedLLMClient(
            [
                action(
                    "finish",
                    "finish",
                    answer="Unsupported answer",
                    evidence_ids=["ev_fake"],
                    limitations=[],
                )
            ]
        ), ControlledVLM()

    resources.client_factory = clients
    run = TextRAGRunner(resources).run(case("text_qa"))
    assert (
        run.status == "citation_validation_failure"
        and run.answer is None
        and run.citations == []
    )
    assert run.task_success is False and run.model_calls == 1 and run.tool_calls == 1


def test_run_counters_do_not_accumulate(resources):
    runner = TextRAGRunner(resources)
    first, second = runner.run(case("text_qa")), runner.run(case("text_qa"))
    assert first.run_id != second.run_id
    assert first.model_calls == second.model_calls == 1
    assert first.tool_calls == second.tool_calls == 1


def test_summary_rejects_fake_and_real_mixture(resources):
    run = TextRAGRunner(resources).run(case("text_qa"))
    with pytest.raises(ValueError, match="Fake and real"):
        summarize(
            [run, run.model_copy(update={"backend": "real", "run_id": "real-run"})]
        )


def test_review_context_mismatch_is_rejected(resources, tmp_path):
    from eval.summarize_results import review_runs

    run = TextRAGRunner(resources).run(case("text_qa"))
    path = tmp_path / "reviews.json"
    path.write_text(
        json.dumps(
            {
                "reviews": [
                    {
                        "run_id": run.run_id,
                        "case_id": "wrong-case",
                        "system": run.system,
                        "reviewer": "test reviewer",
                        "reviewed_at": "2026-10-06T20:00:00+08:00",
                        "task_success": False,
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="case_id/system"):
        review_runs([run], path)
