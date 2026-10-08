"""研究评测结构、污染攻击、恢复接入和预算契约；不作真实质量宣称。"""

import asyncio
import json
from pathlib import Path

import pytest
from test_corpus_isolation import Embedding, Store
from test_recovery_merge import merge_case
from test_recovery_orchestrator import runtime_subject
from test_synthesis_input import bundle

from app.evidence.corpus import load_paper_corpus
from app.research.corpus_validation import corpus_snapshot
from app.research.recovery_models import RecoveryBudget
from app.research.requirements import expand_requirements
from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_models import SynthesisOutcome, SynthesisProposal
from app.research.synthesis_runtime import assemble_report
from app.research.synthesis_validator import SynthesisOutputValidator
from app.retrieval.ingestion import ingest_text_corpus
from app.retrieval.retriever import TextRetriever
from eval.cross_paper import (
    CrossPaperDataset,
    audit_report,
    research_metrics,
    retrieval_metrics,
)
from scripts.evaluate_cross_paper import run_case, scripted_report

ROOT = Path(__file__).resolve().parents[3]


def report_for(source):
    if not source.verified_claims:
        return assemble_report(source, SynthesisOutcome(status="insufficient_evidence"))
    response = scripted_report(
        [{"content": json.dumps({"input": source.model_dump(mode="json")})}], []
    )
    proposal = SynthesisProposal.model_validate(response.tool_calls[0].arguments)
    SynthesisOutputValidator().validate(proposal, source)
    return assemble_report(
        source, SynthesisOutcome(status="completed", proposal=proposal, model_calls=1)
    )


def test_original_fifteen_requirements_and_snapshot_are_frozen():
    dataset = CrossPaperDataset.model_validate_json(
        (ROOT / "eval/datasets/stage_e.json").read_text()
    )
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    snapshot = corpus_snapshot(
        ROOT / "data/papers", corpus, ROOT / "data/research_papers"
    )
    dataset.validate_corpus(corpus, snapshot["snapshot_id"])
    assert len(dataset.cases) == 22
    assert len(dataset.core_requirement_ids) == 15
    assert {c.category for c in dataset.cases} == {
        "single_fact",
        "two_paper",
        "three_paper",
        "insufficient",
        "false_premise",
        "caption_pixels",
        "contamination",
        "recovery",
    }
    with pytest.raises(ValueError, match="snapshot changed"):
        dataset.validate_corpus(corpus, "0" * 64)


def test_eval_source_labels_never_enter_worker_inputs():
    data = CrossPaperDataset.model_validate_json(
        (ROOT / "eval/datasets/stage_e.json").read_text()
    )
    for case in data.cases:
        serialized = json.dumps(
            [t.model_dump(mode="json") for t in case.worker_tasks()]
        )
        assert (
            "expected_behavior" not in serialized and "source_labels" not in serialized
        )
        assert all(
            i not in serialized for ids in case.source_labels.values() for i in ids
        )
        assert all(
            r.status == "pending" and r.claim is None and not r.evidence_ids
            for t in case.worker_tasks()
            for r in t.requirements
        )


@pytest.mark.parametrize("paper_count", [1, 2, 3])
def test_single_two_three_paper_reports_need_each_reviewed_party(paper_count):
    unavailable = {
        (p, d): "insufficient_evidence"
        for p in ["paper-a", "paper-b", "paper-c"][paper_count:]
        for d in ["architecture", "training"]
    }
    plan, workers, reviews, resolver = bundle(statuses=unavailable)
    source = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    report = report_for(source)
    metrics = audit_report(source, report)
    assert len(report.verified_claims) == paper_count * 2
    assert metrics["cross_paper_comparison_count"] == (2 if paper_count >= 2 else 0)
    for section in report.sections:
        for comparison in section.comparisons:
            assert len(comparison.paper_ids) == paper_count


def test_single_party_cannot_pretend_to_support_two_paper_comparison():
    plan, workers, reviews, resolver = bundle()
    source = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    report = report_for(source)
    comparison = (
        report.sections[0]
        .comparisons[0]
        .model_copy(
            update={
                "claim_ids": [
                    source.verified_claims[0].claim_id,
                    source.verified_claims[0].claim_id,
                ],
                "paper_ids": ["paper-a", "paper-b"],
            }
        )
    )
    altered = report.model_copy(
        update={
            "sections": [
                report.sections[0].model_copy(update={"comparisons": [comparison]}),
                *report.sections[1:],
            ]
        }
    )
    with pytest.raises(ValueError):
        audit_report(source, altered)


def test_foreign_paper_evidence_id_is_never_verified():
    plan, workers, reviews, resolver = bundle()
    req = (
        workers[0]
        .requirements[0]
        .model_copy(update={"evidence_ids": ["ev-paper-b-architecture"]})
    )
    workers[0] = workers[0].model_copy(
        update={
            "requirements": [req, workers[0].requirements[1]],
            "observed_evidence_ids": [
                "ev-paper-b-architecture",
                *workers[0].observed_evidence_ids,
            ],
        }
    )
    reviews[0] = reviews[0].model_copy(update={"evidence_ids": req.evidence_ids})
    source = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    assert source.cells[0].status == "review_unavailable"
    assert all(
        c.requirement_id != req.requirement_id
        for c in report_for(source).verified_claims
    )


def test_rejected_claim_text_is_excluded_from_report():
    plan, workers, reviews, resolver = bundle(rejected={("paper-a", "architecture")})
    source = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    report = report_for(source)
    assert workers[0].requirements[0].claim not in report.model_dump_json()
    assert source.cells[0].status == "review_rejected"


def test_recovery_passed_claim_enters_report_and_old_rejection_stays_out():
    merged, source, task, _workers, _reviews = merge_case()
    report = report_for(source)
    assert source.cells[0].status == "verified"
    assert "Narrowed recovery claim." in report.model_dump_json()
    assert (
        task.prior_worker_result.requirements[0].claim not in report.model_dump_json()
    )
    assert merged.history


@pytest.mark.parametrize("status", ["execution_failed", "cancelled"])
def test_failed_recovery_report_preserves_typed_gap(status):
    _, source, *_ = merge_case(status=status)
    report = report_for(source)
    assert report.missing_evidence[0].status == status
    assert all(
        c.requirement_id != source.cells[0].requirement_id
        for c in report.verified_claims
    )


def test_repeat_rejection_does_not_become_verified():
    _, source, *_ = merge_case(review_status="rejected")
    report = report_for(source)
    assert source.cells[0].status == "review_rejected"
    assert "Narrowed recovery claim." not in report.model_dump_json()


def test_no_verified_evidence_is_normal_insufficient_report():
    statuses = {
        (p, d): "insufficient_evidence"
        for p in ["paper-a", "paper-b", "paper-c"]
        for d in ["architecture", "training"]
    }
    plan, workers, reviews, resolver = bundle(statuses=statuses)
    report = report_for(SynthesisInputBuilder(resolver).build(plan, workers, reviews))
    assert report.status == "insufficient_evidence" and not report.verified_claims
    assert all(not s.comparisons for s in report.sections)


def test_report_audit_rejects_modified_authoritative_fact():
    plan, workers, reviews, resolver = bundle()
    source = SynthesisInputBuilder(resolver).build(plan, workers, reviews)
    report = report_for(source)
    changed = report.verified_claims[0].model_copy(
        update={"text": "Invented new technical fact"}
    )
    with pytest.raises(ValueError, match="rewrote"):
        audit_report(
            source,
            report.model_copy(
                update={"verified_claims": [changed, *report.verified_claims[1:]]}
            ),
        )


def test_real_corpus_scripted_two_paper_pipeline_has_zero_provider_calls():
    data = CrossPaperDataset.model_validate_json(
        (ROOT / "eval/datasets/stage_e.json").read_text()
    )
    case = next(c for c in data.cases if c.case_id == "two-paper-bridge")
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    snapshot = corpus_snapshot(
        ROOT / "data/papers", corpus, ROOT / "data/research_papers"
    )
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())
    output = asyncio.run(run_case(case, corpus, snapshot, config, scripted=True))
    assert (
        output["status"] == "scripted_wiring_only" and not output["real_provider_used"]
    )
    assert not output["quality_result_usable"]
    assert output["metrics"]["cross_paper_comparison_count"] == 1
    assert output["metrics"]["total_tool_calls"] == 2
    assert all(
        "chain_of_thought" not in str(w) for w in output["initial_worker_results"]
    )


def test_metrics_counts_and_recovery_diagnostic_are_not_hallucination_rate():
    runtime, plan, workers, reviews, feedback, _ = runtime_subject(synthesis=True)
    outcome = asyncio.run(
        runtime.run(
            plan,
            workers,
            reviews,
            feedback,
            prior_usage=RecoveryBudget(model_calls=0, tool_calls=0),
        )
    )
    metrics = research_metrics(
        outcome,
        initial_workers=workers,
        initial_reviews=reviews,
        wall_time_ms=1,
        corpus_report={
            "corpus_validity_rate": 1,
            "evidence_provenance_validity_rate": 1,
        },
    )
    assert (
        metrics["recovery_attempted_count"] == metrics["recovery_converted_count"] == 1
    )
    assert metrics["unsupported_claim_count"] == 1
    assert "NOT" in metrics["unsupported_claim_detection_method"]
    assert metrics["verified_coverage"] == 1


def test_retrieval_metric_denominator_and_scope_on_fixed_store():
    dataset = CrossPaperDataset.model_validate_json(
        (ROOT / "eval/datasets/stage_e.json").read_text()
    )
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    store = Store()
    ingest_text_corpus(corpus, Embedding(), store)
    metrics = retrieval_metrics(
        [c for c in dataset.cases if c.case_id.startswith("core-")],
        TextRetriever(Embedding(), store),
    )
    assert metrics["sample_count"] == 15
    assert 0 <= metrics["recall_at_5"] <= metrics["recall_at_10"] <= 1
    assert all(
        ids["paper_id"]
        == next(e.paper_id for e in corpus.text_evidence if e.evidence_id == identity)
        for ids in metrics["cases"]
        for identity in ids["retrieved_evidence_ids"]
    )


def test_core_requirement_identity_tampering_is_rejected():
    data = json.loads((ROOT / "eval/datasets/stage_e.json").read_text())
    data["cases"][0]["plan"]["dimensions"][0]["research_question"] = (
        "Altered research request"
    )
    with pytest.raises(ValueError, match="Core cases"):
        CrossPaperDataset.model_validate(data)
    assert (
        len(
            expand_requirements(
                CrossPaperDataset.model_validate_json(
                    (ROOT / "eval/datasets/stage_e.json").read_text()
                ).original_plan
            )
        )
        == 15
    )


def test_default_cli_never_initializes_a_real_model(tmp_path, monkeypatch):
    import sys

    from scripts import evaluate_cross_paper as script

    def forbidden(*args, **kwargs):
        raise AssertionError("Default preflight must not initialize a model")

    target = tmp_path / "preflight.json"
    monkeypatch.setattr(script, "real_retriever", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_cross_paper", "--output", str(target)])
    script.main()
    report = json.loads(target.read_text())
    assert report["status"] == "not_executed" and report["models_initialized"] == 0
    assert report["real_provider_calls"] == 0


def test_pipeline_failure_preserves_executed_cost_and_results(monkeypatch):
    from scripts import evaluate_cross_paper as script

    async def fail(*args, **kwargs):
        raise RuntimeError("Controlled review runtime failure")

    monkeypatch.setattr(script.EvidenceReviewRuntime, "run", fail)
    dataset = CrossPaperDataset.model_validate_json(
        (ROOT / "eval/datasets/stage_e.json").read_text()
    )
    case = next(c for c in dataset.cases if c.case_id == "two-paper-bridge")
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    snapshot = corpus_snapshot(
        ROOT / "data/papers", corpus, ROOT / "data/research_papers"
    )
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())
    report = asyncio.run(run_case(case, corpus, snapshot, config, scripted=True))
    assert report["status"] == "execution_failed"
    assert len(report["initial_worker_results"]) == 2
    assert (
        report["known_initial_model_calls"] == 4
        and report["known_initial_tool_calls"] == 2
    )
    assert report["llm_provider_attempts_observed"] == 0


def test_evaluation_cancellation_propagates_with_partial_snapshot(monkeypatch):
    from scripts import evaluate_cross_paper as script

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(script.AsyncResearchOrchestrator, "run", cancelled)
    dataset = CrossPaperDataset.model_validate_json(
        (ROOT / "eval/datasets/stage_e.json").read_text()
    )
    case = next(c for c in dataset.cases if c.case_id == "two-paper-bridge")
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    snapshot = corpus_snapshot(
        ROOT / "data/papers", corpus, ROOT / "data/research_papers"
    )
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())
    with pytest.raises(asyncio.CancelledError) as error:
        asyncio.run(run_case(case, corpus, snapshot, config, scripted=True))
    assert error.value.evaluation_snapshot["status"] == "cancelled"
    assert error.value.evaluation_snapshot["known_initial_tool_calls"] == 0
