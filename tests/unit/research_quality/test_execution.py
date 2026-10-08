"""执行适配器的输入隔离、失败传播和caption访问边界。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.evidence.corpus import load_paper_corpus
from app.llm.fake import FakeLLMClient
from app.llm.models import LLMResponse, ToolCall
from eval.research_quality.dataset_loader import ROOT, load_dataset
from eval.research_quality.execution import (
    QualityExecutionRunner,
    fixed_case_plan,
    research_prediction,
)
from scripts.finalize_quality_references import OUTPUT_DATASET
from scripts.run_quality_benchmark import selected_cases


class ClosedFake(FakeLLMClient):
    closed = False

    def close(self):
        self.closed = True


def runner(client):
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    return QualityExecutionRunner(corpus, {}, {"runtime": {}}, None, lambda: client)


def test_pilot_covers_four_task_types_and_plan_has_no_gold():
    dataset = load_dataset(OUTPUT_DATASET)
    cases = selected_cases(dataset, "dev", True)
    assert [c.task_type for c in cases] == [
        "claim",
        "reviewer",
        "comparison",
        "abstention",
    ]
    case = cases[2].model_copy(
        update={"reference_answer": "SECRET_GOLD", "expected_behavior": "SECRET_LABEL"}
    )
    plan = fixed_case_plan(case)
    assert plan.paper_ids == case.paper_ids
    assert "SECRET" not in plan.model_dump_json()


@pytest.mark.parametrize(
    "status", ["review_unavailable", "execution_failed", "cancelled"]
)
def test_pipeline_failure_never_becomes_successful_abstention(monkeypatch, status):
    case = next(c for c in load_dataset(OUTPUT_DATASET).cases if c.abstention)
    monkeypatch.setattr(
        "eval.research_quality.execution.ResearchReport.model_validate",
        lambda raw: SimpleNamespace(
            verified_claims=[], missing_evidence=[SimpleNamespace(status=status)]
        ),
    )
    prediction = research_prediction(
        case, "synthetic", {"outcome": {"report": {"marker": True}}}
    )
    assert prediction.answer is None and prediction.abstained is None


def test_reviewer_input_contains_claim_and_evidence_without_expected_label():
    case = next(c for c in load_dataset(OUTPUT_DATASET).cases if c.reviewer)
    client = ClosedFake(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="unit",
                    name="submit_review",
                    arguments={
                        "status": "passed",
                        "reason_code": "evidence_supports_claim",
                    },
                )
            ]
        )
    )
    prediction, _, calls = asyncio.run(runner(client).review_claim(case, "synthetic"))
    payload = json.loads(client.calls[0]["messages"][-1]["content"])
    assert payload["requirement"]["claim"] == case.reviewer.claim
    assert "expected_label" not in json.dumps(
        payload
    ) and "reference_answer" not in json.dumps(payload)
    assert prediction.reviewer_label == "passed" and calls == 1 and client.closed


def test_caption_only_has_no_retrieval_or_pixels_and_preserves_real_decision():
    case = next(
        c
        for c in load_dataset(OUTPUT_DATASET).cases
        if c.abstention and c.abstention.boundary_kind == "caption_only"
    )
    client = ClosedFake(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="unit",
                    name="submit_answer",
                    arguments={
                        "answer": "The caption does not specify exact colors.",
                        "abstained": True,
                        "evidence_ids": case.gold_evidence_ids,
                    },
                )
            ]
        )
    )
    prediction, _, calls = asyncio.run(
        runner(client).caption_boundary(case, "synthetic")
    )
    assert [t["name"] for t in client.calls[0]["tools"]] == ["submit_answer"]
    payload = json.loads(client.calls[0]["messages"][-1]["content"])
    assert all(s["evidence_field"] == "caption" for s in payload["provided_evidence"])
    assert prediction.abstained is True and calls == 1 and client.closed


def test_caption_hallucinated_citation_is_rejected_and_client_closed():
    case = next(
        c
        for c in load_dataset(OUTPUT_DATASET).cases
        if c.abstention and c.abstention.boundary_kind == "caption_only"
    )
    client = ClosedFake(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="unit",
                    name="submit_answer",
                    arguments={
                        "answer": "Unknown",
                        "abstained": True,
                        "evidence_ids": ["FAKE"],
                    },
                )
            ]
        )
    )
    with pytest.raises(ValueError, match="citations"):
        asyncio.run(runner(client).caption_boundary(case, "synthetic"))
    assert client.closed
