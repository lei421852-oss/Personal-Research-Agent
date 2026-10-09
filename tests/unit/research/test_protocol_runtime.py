"""新装配仅增强原 Worker 协议，不变更审核、定向恢复与综合语义。"""

import asyncio
import json
from pathlib import Path

import pytest

from app.evidence.corpus import load_paper_corpus
from app.evidence.models import RetrievedEvidence
from app.llm.fake import ScriptedLLMClient
from app.research.models import ResearchDimension, ResearchPlan
from app.research.protocol_runtime import run_protocol_case
from scripts.evaluate_cross_paper import action, scripted_report

ROOT = Path(__file__).resolve().parents[3]


class Client(ScriptedLLMClient):
    def close(self):
        pass


def inputs():
    corpus = load_paper_corpus(ROOT / "data/papers")
    paper_ids = [m.paper_id for m in corpus.metadata[:2]]
    plan = ResearchPlan(
        question="Compare documented design using supplied prepared evidence.",
        paper_ids=paper_ids,
        dimensions=[
            ResearchDimension(
                dimension_id="design",
                name="Design",
                description="Investigate design.",
                research_question="What design does this paper document?",
            )
        ],
    )
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())

    class Retriever:
        def search_text(self, query, paper_ids, top_k):
            item = next(e for e in corpus.text_evidence if e.paper_id == paper_ids[0])
            return [RetrievedEvidence(evidence=item, retrieval_score=1.0)]

    return plan, corpus, config, Retriever()


def factories(corpus, *, rejected=False, transient=False):
    def search(messages, tools):
        context = json.loads(messages[-1]["content"])
        return action(
            "search_text",
            {
                "query": "Current design question",
                "paper_ids": [context["paper_id"]],
                "top_k": 1,
            },
        )

    def resolve(messages, tools):
        context = json.loads(messages[-1]["content"])
        item = next(
            e for e in corpus.text_evidence if e.paper_id == context["paper_id"]
        )
        prefix = (
            "Narrowed statement: " if "recovery" in context else "Initial statement: "
        )
        return action(
            "resolve_requirement",
            {
                "requirement_id": context["unresolved_requirements"][0][
                    "requirement_id"
                ],
                "status": "supported",
                "claim": prefix + item.text,
                "evidence_ids": [item.evidence_id],
            },
        )

    seen_reviews = set()

    def review(messages, tools):
        task = json.loads(messages[-1]["content"])
        req = task["requirement"]["requirement_id"]
        first = req not in seen_reviews
        seen_reviews.add(req)
        reject = rejected and first
        return action(
            "submit_review",
            {
                "status": "rejected" if reject else "passed",
                "reason_code": "evidence_partially_supports_claim"
                if reject
                else "evidence_supports_claim",
            },
        )

    def connection(messages, tools):
        raise ConnectionError("Fake transient error")

    def client(role):
        if role == "synthesis":
            return Client([scripted_report])
        if role == "reviewer":
            return Client([review])
        sequence = [search, resolve]
        if transient and role == "worker":
            sequence.insert(0, connection)
        return Client(sequence)

    return client


def test_default_workers_review_and_synthesis_complete_without_coverage():
    plan, corpus, config, retriever = inputs()
    result = asyncio.run(
        run_protocol_case(
            plan,
            corpus,
            {"corpus_validity_rate": 1, "evidence_provenance_validity_rate": 1},
            config,
            retriever,
            factories(corpus),
        )
    )
    assert result["status"] == "completed"
    assert result["worker_classes_observed"] == ["PaperResearchWorker"] * 2
    assert result["coverage_worker_enabled"] is False
    assert all(r["status"] == "passed" for r in result["initial_review_results"])
    assert result["outcome"]["report"]["metrics"]["verified_requirements"] == 2
    assert result["metrics"]["cross_paper_comparison_count"] == 1


def test_rejected_claims_recover_once_and_require_re_review():
    plan, corpus, config, retriever = inputs()
    result = asyncio.run(
        run_protocol_case(
            plan,
            corpus,
            {"corpus_validity_rate": 1, "evidence_provenance_validity_rate": 1},
            config,
            retriever,
            factories(corpus, rejected=True),
        )
    )
    assert result["status"] == "completed"
    assert all(r["status"] == "rejected" for r in result["initial_review_results"])
    assert result["outcome"]["metrics"]["recovery_rounds_used"] == 1
    assert result["outcome"]["metrics"]["recovery_passed_count"] == 2
    assert result["worker_classes_observed"] == ["PaperResearchWorker"] * 4
    assert all(
        c["text"].startswith("Narrowed statement:")
        for c in result["outcome"]["report"]["verified_claims"]
    )


def test_transient_retries_remain_in_existing_adapter_and_budget_counts():
    plan, corpus, config, retriever = inputs()
    result = asyncio.run(
        run_protocol_case(
            plan,
            corpus,
            {"corpus_validity_rate": 1, "evidence_provenance_validity_rate": 1},
            config,
            retriever,
            factories(corpus, transient=True),
        )
    )
    assert result["status"] == "completed"
    assert all(w["trace"]["retry_count"] == 1 for w in result["initial_worker_results"])
    assert all(w["trace"]["model_calls"] == 3 for w in result["initial_worker_results"])


def test_cancellation_propagates_with_typed_snapshot():
    async def scenario():
        plan, corpus, config, retriever = inputs()
        started = asyncio.Event()

        class Waiting(Client):
            async def complete(self, **kwargs):
                started.set()
                await asyncio.Event().wait()

        def factory(role):
            return Waiting([]) if role == "worker" else Client([])

        running = asyncio.create_task(
            run_protocol_case(plan, corpus, {}, config, retriever, factory)
        )
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError) as exc:
            await running
        assert exc.value.evaluation_snapshot["status"] == "cancelled"

    asyncio.run(scenario())
