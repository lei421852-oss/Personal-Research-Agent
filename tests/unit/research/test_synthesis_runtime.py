"""成功、取消及确定性降级均保留真实矩阵，覆盖率不被生成成功率替代。"""

import asyncio
import hashlib
import json
import sys

import pytest
from test_synthesis_agent import fail, response, subject
from test_synthesis_input import bundle, proposal

from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_runtime import SynthesisRuntime
from scripts import smoke_research_synthesis as smoke


def run_report(*, degraded=False, statuses=None, rejected=(), unavailable=()):
    plan, workers, reviews, resolver = bundle(
        statuses=statuses, rejected=rejected, unavailable=unavailable
    )
    builder = SynthesisInputBuilder(resolver)
    source = builder.build(plan, workers, reviews)
    agent, client, _ = subject(
        [response({})] * 3 if degraded else [response(proposal(source))]
    )
    runtime = SynthesisRuntime(builder, agent)
    report = asyncio.run(runtime.run(plan, workers, reviews))
    return report, source, client


def test_complete_report_preserves_authoritative_claims_and_citations():
    report, source, _ = run_report()
    assert report.status == "completed" and not report.generation_degraded
    assert report.verified_claims == source.verified_claims
    assert sum(len(s.cells) for s in report.sections) == 6
    assert (
        report.metrics.evidence_coverage
        == report.metrics.report_generation_success_rate
        == 1
    )


def test_three_papers_partial_verified_report_keeps_missing_matrix_and_separate_rates():
    statuses = {
        (p, d): "insufficient_evidence"
        for p in ["paper-b", "paper-c"]
        for d in ["architecture", "training"]
    }
    report, _, _ = run_report(statuses=statuses)
    assert len(report.paper_ids) == 3 and len(report.sections) == 2
    assert len(report.verified_claims) == 2 and len(report.missing_evidence) == 4
    assert all(s.comparisons == [] for s in report.sections)
    assert report.metrics.resolution_rate == report.metrics.review_pass_rate == 1
    assert report.metrics.evidence_coverage == pytest.approx(1 / 3)
    assert report.metrics.report_generation_success_rate == 1
    assert "Authoritative claim paper-b" not in report.model_dump_json()


def test_all_missing_yields_business_report_without_model_attempt():
    statuses = {
        (p, d): "insufficient_evidence"
        for p in ["paper-a", "paper-b", "paper-c"]
        for d in ["architecture", "training"]
    }
    report, _, client = run_report(statuses=statuses)
    assert report.status == "insufficient_evidence" and not report.generation_degraded
    assert report.summary and "没有足够" in report.summary
    assert len(report.missing_evidence) == 6 and not client.calls
    assert report.metrics.resolution_rate == 1 and report.metrics.evidence_coverage == 0
    assert report.metrics.report_generation_success_rate is None
    assert report.metrics.review_pass_rate is None


def test_protocol_exhaustion_delivers_labelled_fallback_not_model_success():
    report, source, _ = run_report(degraded=True)
    assert report.status == "generation_degraded" and report.generation_degraded
    assert report.issue.code == "protocol_correction_exhausted"
    assert report.verified_claims == source.verified_claims
    assert len(report.sections) == 2 and sum(len(s.cells) for s in report.sections) == 6
    assert report.metrics.report_generation_success_rate == 0
    assert report.metrics.evidence_coverage == 1
    assert report.metrics.synthesis_model_calls == 3


def test_rejected_claims_are_not_in_report_and_reviewer_failure_is_separate():
    report, _, _ = run_report(
        rejected={("paper-a", "architecture")}, unavailable={("paper-b", "training")}
    )
    assert report.metrics.rejected_requirements == 1
    assert {m.status for m in report.missing_evidence} == {
        "review_rejected",
        "review_unavailable",
    }
    assert "Authoritative claim paper-a architecture." not in report.model_dump_json()
    assert report.metrics.review_pass_rate == pytest.approx(4 / 5)


def test_provider_timeout_preserves_verified_facts_in_fallback():
    plan, workers, reviews, resolver = bundle()
    builder = SynthesisInputBuilder(resolver)
    source = builder.build(plan, workers, reviews)
    agent, _, _ = subject([fail(TimeoutError())] * 3)
    report = asyncio.run(SynthesisRuntime(builder, agent).run(plan, workers, reviews))
    assert report.status == "generation_degraded"
    assert report.verified_claims == source.verified_claims
    assert report.metrics.provider_retries == 2


def test_cancellation_propagates_and_retains_distinct_cancelled_fact_snapshot():
    async def scenario():
        entered = asyncio.Event()

        class Client:
            async def complete(self, *, messages, tools):
                entered.set()
                await asyncio.Event().wait()

        plan, workers, reviews, resolver = bundle()
        builder = SynthesisInputBuilder(resolver)
        agent, _, _ = subject([], client=Client())
        runtime = SynthesisRuntime(builder, agent)
        task = asyncio.create_task(runtime.run(plan, workers, reviews))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        report = runtime.last_report
        assert report.status == "cancelled" and report.issue.code == "cancelled"
        assert len(report.verified_claims) == 6
        assert report.metrics.synthesis_model_calls == 1
        assert report.metrics.report_generation_success_rate == 0
        assert not report.generation_degraded

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_hash", [False, True])
def test_smoke_source_binding_and_validation_mode_never_initialize_provider(
    tmp_path, monkeypatch, bad_hash
):
    plan, workers, reviews, resolver = bundle()
    plan_path, worker_path, review_path = [
        tmp_path / name for name in ["plan.json", "workers.json", "reviews.json"]
    ]
    output = tmp_path / "validated.json"
    plan_path.write_text(plan.model_dump_json())
    worker_path.write_text(
        json.dumps({"worker_results": [w.model_dump(mode="json") for w in workers]})
    )
    source_hash = hashlib.sha256(worker_path.read_bytes()).hexdigest()
    review_path.write_text(
        json.dumps(
            {
                "source_worker_report_sha256": "wrong-hash"
                if bad_hash
                else source_hash,
                "results": [r.model_dump(mode="json") for r in reviews],
            }
        )
    )

    async def forbidden(*args):
        pytest.fail("Validation must not initialize or invoke a Provider")

    monkeypatch.setattr(smoke, "run_real", forbidden)
    monkeypatch.setattr(smoke, "load_paper_corpus", lambda _: None)
    monkeypatch.setattr(smoke, "EvidenceResolver", lambda _: resolver)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "--plan",
            str(plan_path),
            "--worker-results",
            str(worker_path),
            "--review-results",
            str(review_path),
            "--validate-only",
            "--env-file",
            str(tmp_path / "absent.env"),
            "--output",
            str(output),
        ],
    )
    if bad_hash:
        from app.research.synthesis_input import SynthesisInputError

        with pytest.raises(SynthesisInputError, match="worker_review_source_mismatch"):
            smoke.main()
        assert not output.exists()
    else:
        smoke.main()
        saved = json.loads(output.read_text())
        assert (
            saved["mode"] == "validated_not_executed" and saved["real_model_calls"] == 0
        )
        assert saved["input_counts"] == {"cells": 6, "verified": 6, "missing": 0}


def test_markdown_fallback_keeps_claim_body_and_explicit_degraded_status():
    report, source, _ = run_report(degraded=True)
    text = smoke.render_markdown(report)
    assert "generation_degraded" in text
    assert all(c.text in text for c in source.verified_claims)
    assert all(
        e.evidence_id in text for c in source.verified_claims for e in c.evidence
    )
