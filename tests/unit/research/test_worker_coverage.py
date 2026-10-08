"""覆盖协议的确定性回归；伪模型验证执行边界，不代表语义质量评测。"""

import asyncio
import hashlib
import json
from copy import deepcopy

import pytest
from test_paper_worker import (
    EV,
    PAPER,
    FakeSearch,
    Resolver,
    action,
    last_context,
    search,
    subject,
)

from app.evidence.models import TextEvidence
from app.llm.models import LLMResponse
from app.research.coverage_models import ASPECTS_BY_DIMENSION, DEFAULT_ASPECTS
from app.research.coverage_worker import CoverageAwarePaperWorker
from app.research.models import (
    EvidenceRequirement,
    ResearchDimension,
    ResearchPlan,
    WorkerTask,
)
from app.research.orchestrator import AsyncResearchOrchestrator
from app.research.requirements import expand_requirements, group_worker_tasks
from app.research.review_models import ReviewResult, ReviewTask
from app.research.review_validation import DeterministicReviewValidator
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.synthesis_input import SynthesisInputBuilder
from app.tools.observations import ObservationIssue, ToolObservation
from eval.research_quality.coverage_comparison import (
    CoverageComparisonRequest,
    build_comparison_plan,
    comparison_worker_tasks,
    prepare_comparison,
    summarize_coverage_comparison,
)


def coverage_task(*dimensions, paper_id=PAPER):
    dimensions = dimensions or ("language_model",)
    return WorkerTask(
        worker_task_id=f"worker-{paper_id}",
        paper_id=paper_id,
        requirements=[
            EvidenceRequirement(
                requirement_id=f"req-{paper_id}-{index}",
                paper_id=paper_id,
                dimension_id=dimension,
                question=f"Compare this paper and its peer on {dimension}.",
            )
            for index, dimension in enumerate(dimensions)
        ],
    )


def fact(
    dimension, aspect_id, *, evidence_id=EV.evidence_id, paper_id=PAPER, **updates
):
    return {
        "subject_paper_id": paper_id,
        "dimension_id": dimension,
        "text": f"Synthetic local {dimension} fact covering {aspect_id}.",
        "evidence_ids": [evidence_id],
        **updates,
    }


def resolution(
    dimension="language_model",
    *,
    index=0,
    missing=(),
    status="supported",
    paper_id=PAPER,
):
    return {
        "requirement_id": f"req-{paper_id}-{index}",
        "status": status,
        "coverage": [
            {"aspect_id": aspect_id, "status": "missing"}
            if aspect_id in missing
            else {
                "aspect_id": aspect_id,
                "status": "covered",
                "fact": fact(dimension, aspect_id, paper_id=paper_id),
            }
            for aspect_id in ASPECTS_BY_DIMENSION.get(dimension, DEFAULT_ASPECTS)
        ],
        "stages": [],
    }


def submit(value):
    return action("resolve_requirement", **value)


def coverage_subject(responses, **kwargs):
    legacy, client, tool = subject(responses, **kwargs)
    worker = CoverageAwarePaperWorker(
        legacy.llm, legacy.tools, legacy.resolver, legacy.config
    )
    return worker, client, tool


def found(*evidence):
    return ToolObservation(
        status="success",
        tool_name="search_text",
        summary="Prepared synthetic local evidence.",
        payload={
            "evidence": [
                item.model_dump() | {"retrieval_score": 0.9} for item in evidence
            ]
        },
    )


def gaps(dimension="language_model", *, index=0, paper_id=PAPER):
    return resolution(
        dimension,
        index=index,
        paper_id=paper_id,
        missing=ASPECTS_BY_DIMENSION.get(dimension, DEFAULT_ASPECTS),
        status="insufficient_evidence",
    )


@pytest.mark.parametrize(
    "dimension",
    ["vision_encoder", "vision_language_bridge", "language_model", "custom_dimension"],
)
def test_complete_local_coverage_preserves_legacy_supported_contract(dimension):
    worker, client, tool = coverage_subject([search(), submit(resolution(dimension))])
    assigned = coverage_task(dimension)
    result = asyncio.run(worker.run(assigned))
    assert result.status == "completed"
    assert result.requirements[0].status == "supported"
    assert result.requirements[0].evidence_ids == [EV.evidence_id]
    assert result.requirements[0].question == assigned.requirements[0].question
    assert (
        result.trace.model_calls == 2
        and result.trace.tool_calls == len(tool.calls) == 1
    )
    assert worker.last_coverage_report["requirements"][0]["complete"] is True
    context = last_context(client)
    assignment = context["coverage_assignments"][0]
    assert assignment["dimension_id"] == dimension and assignment["paper_id"] == PAPER
    assert assignment["expected_aspects"] == list(
        ASPECTS_BY_DIMENSION.get(dimension, DEFAULT_ASPECTS)
    )
    assert "global comparison" in client.calls[0]["messages"][0]["content"].lower()
    assert "performance numbers" in client.calls[0]["messages"][0]["content"]


def test_foreign_subject_is_rejected_then_policy_corrects_it():
    bad = resolution()
    bad["coverage"][0]["fact"]["subject_paper_id"] = "peer-paper"
    worker, client, tool = coverage_subject(
        [search(), submit(bad), submit(resolution())]
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "supported" and len(tool.calls) == 1
    context = last_context(client)
    assert len(context["unresolved_requirements"]) == 1
    assert "invalid_arguments" in json.dumps(context["recent_observations"])
    assert (
        worker.last_coverage_report["requirements"][0]["coverage"][0]["fact"][
            "subject_paper_id"
        ]
        == PAPER
    )


@pytest.mark.parametrize("corruption", ["unobserved", "unknown", "wrong_dimension"])
def test_invalid_citation_or_dimension_cannot_become_a_supported_claim(corruption):
    unseen = EV.model_copy(update={"evidence_id": "ev-unobserved"})
    bad = resolution()
    candidate = bad["coverage"][0]["fact"]
    if corruption == "wrong_dimension":
        candidate["dimension_id"] = "vision_encoder"
    else:
        candidate["evidence_ids"] = [
            "ev-unobserved" if corruption == "unobserved" else "ev-unknown"
        ]
    worker, client, _ = coverage_subject(
        [search(), submit(bad), submit(gaps())], resolver=Resolver((EV, unseen))
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "insufficient_evidence"
    assert (
        result.requirements[0].claim is None
        and result.requirements[0].evidence_ids == []
    )
    assert "invalid_arguments" in json.dumps(
        last_context(client)["recent_observations"]
    )


def test_frozen_llm_does_not_complete_missing_model_family():
    partial = resolution(missing=("model_family",))
    partial["coverage"][2]["fact"]["text"] = "The language model is frozen."
    worker, client, _ = coverage_subject(
        [
            search(),
            submit(partial),
            submit(partial | {"status": "insufficient_evidence"}),
        ]
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "insufficient_evidence"
    assert result.requirements[0].claim is None
    context = last_context(client)
    assert context["unresolved_requirements"]
    assert context["coverage_assignments"][0]["current_assessment"]["complete"] is False
    assert "model_family" in json.dumps(context["recent_observations"])
    assert worker.last_coverage_report["requirements"][0]["retained_gaps"] is True


def test_bridge_gap_feedback_allows_specific_additional_lookup_then_completion():
    interface = TextEvidence(
        evidence_id="ev-interface",
        paper_id=PAPER,
        page=3,
        text="A synthetic connector converts visual features into the language-model input.",
    )
    partial = resolution("vision_language_bridge", missing=("language_interface",))
    complete = resolution("vision_language_bridge")
    complete["coverage"][2]["fact"]["evidence_ids"] = [interface.evidence_id]
    worker, client, tool = coverage_subject(
        [
            search(),
            submit(partial),
            search("Document the language-model input interface"),
            submit(complete),
        ],
        tool=FakeSearch([found(EV), found(interface)]),
        resolver=Resolver((EV, interface)),
    )
    result = asyncio.run(worker.run(coverage_task("vision_language_bridge")))
    assert result.requirements[0].status == "supported"
    assert result.requirements[0].evidence_ids == [
        EV.evidence_id,
        interface.evidence_id,
    ]
    assert len(tool.calls) == result.trace.tool_calls == 2
    assert result.trace.model_calls == 4
    assert "requirement_coverage_incomplete" in json.dumps(client.calls[2]["messages"])
    metrics = worker.last_coverage_report["metrics"]
    assert (
        metrics["coverage_rejections"] == 1 and metrics["extra_lookup_observed"] is True
    )
    assert (
        metrics["additional_tool_calls"] == 1 and metrics["additional_model_calls"] == 2
    )
    assert result.trace.actions[1]["coverage_submission"]["accepted"] is False
    assert (
        result.trace.actions[-1]["coverage_final"]["requirements"][0]["complete"]
        is True
    )


@pytest.mark.parametrize(
    "dimension,stage_keys",
    [
        ("frozen_trainable_scope", ("frozen_modules", "updated_modules")),
        ("training_objective", ("losses", "optimization_targets")),
    ],
)
def test_stage_two_facts_leave_stage_one_missing_and_diagnostic_only(
    dimension, stage_keys
):
    partial = resolution(dimension)
    partial["stages"] = [
        {
            "stage_id": "stage_1",
            "name": "First documented stage",
            "aspects": [{"aspect_id": key, "status": "missing"} for key in stage_keys],
        },
        {
            "stage_id": "stage_2",
            "name": "Second documented stage",
            "aspects": [
                {"aspect_id": key, "status": "covered", "fact": fact(dimension, key)}
                for key in stage_keys
            ],
        },
    ]
    worker, client, _ = coverage_subject(
        [
            search(),
            submit(partial),
            submit(partial | {"status": "insufficient_evidence"}),
        ]
    )
    result = asyncio.run(worker.run(coverage_task(dimension)))
    assert result.requirements[0].status == "insufficient_evidence"
    assert (
        result.requirements[0].claim is None
        and result.requirements[0].evidence_ids == []
    )
    sidecar = worker.last_coverage_report["requirements"][0]
    assert (
        sidecar["complete"] is False
        and sidecar["stages"][0]["aspects"][0]["status"] == "missing"
    )
    assert sidecar["stages"][1]["aspects"][0]["status"] == "covered"
    assert "stage_1:" in json.dumps(last_context(client)["recent_observations"])


def test_documented_training_stages_require_explicit_stage_details():
    partial = resolution("training_objective")
    worker, client, _ = coverage_subject(
        [search(), submit(partial), submit(gaps("training_objective"))]
    )
    result = asyncio.run(worker.run(coverage_task("training_objective")))
    assert result.requirements[0].status == "insufficient_evidence"
    assert "stage_details" in json.dumps(last_context(client)["recent_observations"])


def test_incomplete_coverage_budget_exhaustion_remains_execution_failed():
    worker, _, tool = coverage_subject(
        [search(), submit(resolution(missing=("model_family",)))],
        config=ResearchRuntimeConfig(max_model_calls_per_worker=2),
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.status == "failed" and result.error.code == "budget_exhausted"
    assert result.requirements[0].status == "execution_failed"
    assert result.trace.model_calls == 2 and len(tool.calls) == 1
    assert worker.last_coverage_report["requirements"][0]["retained_gaps"] is True


def test_repeated_partial_submissions_are_bounded_without_extra_retrieval():
    partial = submit(resolution(missing=("model_family",)))
    worker, _, tool = coverage_subject(
        [search(), partial, partial, partial],
        config=ResearchRuntimeConfig(max_model_calls_per_worker=4),
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.error.code == "budget_exhausted" and result.trace.model_calls == 4
    assert result.requirements[0].status == "execution_failed" and len(tool.calls) == 1
    assert worker.last_coverage_report["metrics"]["coverage_rejections"] == 3


@pytest.mark.parametrize("quote", [None, "This quote was not observed."])
def test_exclusive_assertions_need_an_exact_observed_quote(quote):
    bad = resolution()
    bad["coverage"][2]["fact"].update(assertion_kind="exclusive", support_quote=quote)
    worker, client, _ = coverage_subject([search(), submit(bad), submit(gaps())])
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "insufficient_evidence"
    assert "invalid_arguments" in json.dumps(
        last_context(client)["recent_observations"]
    )


def test_documented_only_statement_is_accepted_instead_of_keyword_banned():
    text = "Only the connector is updated during this documented stage."
    evidence = EV.model_copy(update={"text": text})
    value = resolution()
    value["coverage"][2]["fact"].update(
        text=text, assertion_kind="exclusive", support_quote=text
    )
    worker, _, _ = coverage_subject(
        [search(), submit(value)],
        resolver=Resolver((evidence,)),
        tool=FakeSearch([found(evidence)]),
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert (
        result.requirements[0].status == "supported"
        and text in result.requirements[0].claim
    )


def test_not_applicable_has_positive_evidence_and_is_included_in_formal_citations():
    text = "This system has no language decoder; decoder architecture is inapplicable."
    positive = EV.model_copy(update={"evidence_id": "ev-applicability", "text": text})
    value = resolution()
    value["coverage"][1] = {
        "aspect_id": "architecture",
        "status": "not_applicable",
        "fact": fact(
            "language_model",
            "architecture",
            evidence_id=positive.evidence_id,
            text=text,
            support_quote=text,
        ),
    }
    worker, _, _ = coverage_subject(
        [search(), submit(value)],
        resolver=Resolver((EV, positive)),
        tool=FakeSearch([found(EV, positive)]),
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "supported"
    assert positive.evidence_id in result.requirements[0].evidence_ids
    assert (
        worker.last_coverage_report["requirements"][0]["coverage"][1]["status"]
        == "not_applicable"
    )


def test_empty_search_does_not_establish_not_applicable():
    empty = ToolObservation(
        status="empty",
        tool_name="search_text",
        summary="No evidence was found.",
        payload={"evidence": []},
        issue=ObservationIssue(code="empty_result", retryable=False),
    )
    bad = resolution()
    bad["coverage"][0]["status"] = "not_applicable"
    worker, client, _ = coverage_subject(
        [search(), submit(bad), submit(gaps())], tool=FakeSearch([empty])
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "insufficient_evidence"
    assert result.observed_evidence_ids == []
    assert "invalid_arguments" in json.dumps(
        last_context(client)["recent_observations"]
    )


def test_batch_with_invalid_identity_has_no_partial_formal_or_sidecar_commit():
    first = resolution()
    second = resolution("vision_encoder", index=1)
    second["coverage"][0]["fact"]["subject_paper_id"] = "peer-paper"
    invalid = action("resolve_requirements", resolutions=[first, second])
    valid = action(
        "resolve_requirements", resolutions=[first, gaps("vision_encoder", index=1)]
    )
    worker, client, _ = coverage_subject([search(), invalid, valid])
    result = asyncio.run(worker.run(coverage_task("language_model", "vision_encoder")))
    context = last_context(client)
    assert context["resolved_requirements"] == []
    assert all(
        value["current_assessment"] is None for value in context["coverage_assignments"]
    )
    assert [r.status for r in result.requirements] == [
        "supported",
        "insufficient_evidence",
    ]


def test_batch_with_gap_keeps_all_requirements_pending_until_policy_resubmits():
    complete = resolution()
    partial = resolution("vision_encoder", index=1, missing=("feature_output",))
    worker, client, _ = coverage_subject(
        [
            search(),
            action("resolve_requirements", resolutions=[complete, partial]),
            action(
                "resolve_requirements",
                resolutions=[complete, partial | {"status": "insufficient_evidence"}],
            ),
        ]
    )
    result = asyncio.run(worker.run(coverage_task("language_model", "vision_encoder")))
    assert last_context(client)["resolved_requirements"] == []
    assert [r.status for r in result.requirements] == [
        "supported",
        "insufficient_evidence",
    ]


def test_rejected_reasoning_fields_and_model_content_never_enter_trace():
    bad = resolution() | {
        "reasoning": "private hidden argument rationale",
        "chain_of_thought": "private hidden chain",
        "internal_thought": "private hidden thought",
    }
    worker, _, _ = coverage_subject([search(), submit(bad), submit(resolution())])
    result = asyncio.run(worker.run(coverage_task()))
    serialized = result.model_dump_json()
    for secret in (
        "private hidden argument rationale",
        "private hidden chain",
        "private hidden thought",
        "private hidden reasoning body",
    ):
        assert secret not in serialized
    assert (
        result.trace.actions[-1]["coverage_final"]["requirements"][0]["dimension_id"]
        == "language_model"
    )


def test_multiple_action_correction_preserves_model_and_tool_budget_semantics():
    multiple = LLMResponse(
        content="private hidden reasoning body",
        tool_calls=[search().tool_calls[0], submit(resolution()).tool_calls[0]],
    )
    worker, client, tool = coverage_subject([multiple, search(), submit(resolution())])
    result = asyncio.run(worker.run(coverage_task()))
    assert result.status == "completed" and result.trace.protocol_corrections == 1
    assert result.trace.model_calls == len(client.calls) == 3
    assert result.trace.tool_calls == len(tool.calls) == 1
    assert result.trace.actions[0]["execution_status"] == "not_executed"
    assert "multiple_actions" in json.dumps(client.calls[1]["messages"])


def test_concurrent_reentry_does_not_clear_an_active_coverage_assessment():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        class Client:
            def __init__(self):
                self.responses = iter(
                    [search(), submit(resolution(missing=("model_family",)))]
                )

            async def complete(self, *, messages, tools):
                try:
                    return next(self.responses)
                except StopIteration:
                    entered.set()
                    await release.wait()
                    return submit(gaps())

        worker, _, _ = coverage_subject([], client=Client())
        run = asyncio.create_task(worker.run(coverage_task()))
        await asyncio.wait_for(entered.wait(), 1)
        before = deepcopy(worker._assessments)
        with pytest.raises(RuntimeError, match="concurrent"):
            await worker.run(coverage_task())
        assert worker._assessments == before and before
        release.set()
        result = await run
        assert result.requirements[0].status == "insufficient_evidence"
        assert worker.last_coverage_report["metrics"]["coverage_rejections"] == 1

    asyncio.run(scenario())


def test_parallel_pool_keeps_coverage_and_observed_evidence_paper_local():
    async def scenario():
        config = ResearchRuntimeConfig(max_concurrent_workers=2)
        gate = ResourceGate(config)
        peer = TextEvidence(
            evidence_id="ev-peer",
            paper_id="paper-peer",
            page=1,
            text="Synthetic peer paper evidence.",
        )
        resolver = Resolver((EV, peer))
        workers = {}

        def factory(assigned):
            is_local = assigned.paper_id == PAPER
            value = (
                resolution(paper_id=assigned.paper_id)
                if is_local
                else gaps(paper_id=assigned.paper_id)
            )
            responses = [search(), submit(value)] if is_local else [submit(value)]
            worker, _, _ = coverage_subject(
                responses, resolver=resolver, config=config, gate=gate
            )
            workers[assigned.paper_id] = worker
            return worker

        pool = AsyncResearchOrchestrator(factory, config, gate)
        results = await pool.run(
            [coverage_task(), coverage_task(paper_id=peer.paper_id)]
        )
        assert [result.requirements[0].status for result in results] == [
            "supported",
            "insufficient_evidence",
        ]
        assert results[1].observed_evidence_ids == []
        assert (
            workers[PAPER].last_coverage_report["requirements"][0]["complete"] is True
        )
        assert (
            workers[peer.paper_id].last_coverage_report["requirements"][0]["complete"]
            is False
        )
        assert (
            workers[PAPER].last_coverage_report
            is not workers[peer.paper_id].last_coverage_report
        )

    asyncio.run(scenario())


def test_supported_coverage_result_remains_compatible_with_review_and_synthesis():
    plan = ResearchPlan(
        question="Compare the documented model family.",
        paper_ids=[PAPER],
        dimensions=[
            ResearchDimension(
                dimension_id="language_model",
                name="Language model",
                description="Compare documented model properties.",
                research_question="Which model family, architecture and update state are documented?",
            )
        ],
    )
    assigned = group_worker_tasks(expand_requirements(plan))[0]
    value = resolution() | {"requirement_id": assigned.requirements[0].requirement_id}
    worker, _, _ = coverage_subject([search(), submit(value)])
    result = asyncio.run(worker.run(assigned))
    checked = DeterministicReviewValidator(worker.resolver).validate(
        result, result.requirements[0]
    )
    assert isinstance(checked, ReviewTask)
    review = ReviewResult(
        worker_task_id=result.worker_task_id,
        requirement_id=checked.requirement.requirement_id,
        paper_id=PAPER,
        status="passed",
        reason_code="evidence_supports_claim",
        claim=checked.requirement.claim,
        evidence_ids=checked.requirement.evidence_ids,
        validation_status="passed",
    )
    source = SynthesisInputBuilder(worker.resolver).build(plan, [result], [review])
    assert len(source.verified_claims) == 1
    assert source.verified_claims[0].text == result.requirements[0].claim
    assert source.allowed_evidence_ids == [EV.evidence_id]


def test_empty_comparison_metrics_have_explicit_zero_denominators_and_null_quality():
    metrics = summarize_coverage_comparison([], [])
    assert metrics["complete_requirement_rate"] == {
        "value": None,
        "numerator": 0,
        "denominator": 0,
    }
    assert metrics["attribute_coverage_rate"]["value"] is None
    assert all(
        value["value"] is None and value["denominator"] == 0
        for value in metrics["semantic_quality"].values()
    )
    assert metrics["worker_cost"]["denominator"] == 0


def test_structural_complete_coverage_does_not_imply_semantic_quality_without_labels():
    value = resolution() | {"complete": True}
    metrics = summarize_coverage_comparison([], [value])
    assert metrics["complete_requirement_rate"]["value"] == 1
    assert all(value["value"] is None for value in metrics["semantic_quality"].values())


def test_comparison_preparation_whitelists_runtime_input_and_preserves_source_hashes(
    tmp_path,
):
    request = {
        "case_id": "synthetic-comparison",
        "question": "Compare the language models of the supplied papers.",
        "paper_ids": ["paper-a", "paper-b"],
        "dimension": "language_model",
    }
    projection = request | {
        "reference_answer": "secret gold answer",
        "gold_evidence_ids": ["ev-gold"],
        "score_notes": "secret scoring notes",
    }
    row = {
        "case_id": request["case_id"],
        "task_type": "comparison",
        "case_sha256": "synthetic-frozen-case-hash",
        "input_projection": projection,
        "detail": {
            "plan": build_comparison_plan(
                CoverageComparisonRequest(**request)
            ).model_dump(mode="json"),
            "configuration": {
                "runtime": ResearchRuntimeConfig().model_dump(mode="json")
            },
            "corpus_snapshot": {"fingerprint": "fixed-synthetic-corpus"},
        },
    }
    executions = tmp_path / "executions.jsonl"
    subset = tmp_path / "subset.jsonl"
    text = json.dumps(row) + "\n"
    executions.write_text(text)
    subset.write_text(text)
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"cases": [{"case_id": request["case_id"]}]}))
    output = tmp_path / "new-preparation"
    before = hashlib.sha256(executions.read_bytes()).hexdigest()
    manifest = prepare_comparison(
        executions, findings, output, diagnostic_executions=subset
    )
    runtime = json.loads((output / "requests.jsonl").read_text())
    assert runtime == request
    assert "secret gold answer" not in json.dumps(runtime)
    assert set(runtime) == {"case_id", "question", "paper_ids", "dimension"}
    assert (
        manifest["source_execution_file_sha256"]
        == before
        == hashlib.sha256(executions.read_bytes()).hexdigest()
    )
    assert (
        manifest["requests_sha256"]
        == hashlib.sha256((output / "requests.jsonl").read_bytes()).hexdigest()
    )
    assert manifest["provider_calls"] == manifest["retrieval_calls"] == 0
    tasks = comparison_worker_tasks(CoverageComparisonRequest(**runtime))
    assert len(tasks) == 2 and all(len(task.requirements) == 1 for task in tasks)
    assert "secret gold answer" not in json.dumps(
        [task.model_dump(mode="json") for task in tasks]
    )
    with pytest.raises(ValueError, match="output_exists"):
        prepare_comparison(executions, findings, output)


def test_comparison_runtime_request_rejects_reference_answer_fields():
    with pytest.raises(ValueError):
        CoverageComparisonRequest(
            case_id="case",
            question="Compare papers.",
            paper_ids=["paper-a", "paper-b"],
            dimension="language_model",
            reference_answer="gold must not enter runtime",
        )


@pytest.mark.parametrize(
    "corruption", ["missing_sidecar", "foreign_requirement", "duplicate_worker"]
)
def test_comparison_summary_requires_exact_worker_requirement_authority(corruption):
    worker, _, _ = coverage_subject([search(), submit(resolution())])
    result = asyncio.run(worker.run(coverage_task()))
    workers = [result]
    coverage = [deepcopy(worker.last_coverage_report)]
    if corruption == "missing_sidecar":
        coverage = []
    elif corruption == "foreign_requirement":
        coverage[0]["requirements"][0]["requirement_id"] = "req-unrelated"
    else:
        workers.append(result)
    with pytest.raises(ValueError):
        summarize_coverage_comparison(workers, coverage)


def test_dependency_retry_failure_is_not_claimed_as_coverage_insufficiency():
    tool = FakeSearch([TimeoutError(), TimeoutError(), TimeoutError()])
    worker, _, _ = coverage_subject([search()], tool=tool)
    result = asyncio.run(worker.run(coverage_task()))
    assert result.status == "failed"
    assert result.requirements[0].status == "execution_failed"
    assert result.trace.tool_calls == len(tool.calls) == 3
    assert result.trace.retry_count == 2
    sidecar = worker.last_coverage_report["requirements"][0]
    assert sidecar["formal_status"] == "execution_failed"
    assert sidecar["complete"] is False and sidecar["retained_gaps"] is True


def test_cancellation_preserves_completed_coverage_and_pending_requirement_state():
    async def scenario():
        entered = asyncio.Event()

        class Client:
            def __init__(self):
                self.responses = iter([search(), submit(resolution())])

            async def complete(self, *, messages, tools):
                try:
                    return next(self.responses)
                except StopIteration:
                    entered.set()
                    await asyncio.Event().wait()

        worker, _, _ = coverage_subject([], client=Client())
        run = asyncio.create_task(
            worker.run(coverage_task("language_model", "vision_encoder"))
        )
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        result = worker.last_result
        assert result.status == "cancelled"
        assert [r.status for r in result.requirements] == ["supported", "cancelled"]
        coverage = worker.last_coverage_report["requirements"]
        assert coverage[0]["complete"] is True
        assert (
            coverage[1]["complete"] is False
            and coverage[1]["formal_status"] == "cancelled"
        )

    asyncio.run(scenario())


def test_insufficient_evidence_without_an_explicit_gap_is_rejected():
    worker, client, _ = coverage_subject(
        [search(), submit(resolution(status="insufficient_evidence")), submit(gaps())]
    )
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "insufficient_evidence"
    assert "invalid_arguments" in json.dumps(
        last_context(client)["recent_observations"]
    )
    assert worker.last_coverage_report["requirements"][0]["retained_gaps"] is True


def test_declared_complete_model_cannot_bypass_missing_aspect_validation():
    bad = resolution()
    bad["coverage"] = bad["coverage"][1:]
    worker, client, _ = coverage_subject([search(), submit(bad), submit(gaps())])
    result = asyncio.run(worker.run(coverage_task()))
    assert result.requirements[0].status == "insufficient_evidence"
    assert "invalid_arguments" in json.dumps(
        last_context(client)["recent_observations"]
    )


@pytest.mark.parametrize("same_field", [True, False])
def test_duplicate_semantic_case_never_inflates_assessment_denominator(same_field):
    first = {
        "case_id": "same-case",
        "source": "ai",
        "assessor": "synthetic-assessor",
        "final_comparison_quality": True,
    }
    second = (
        first
        if same_field
        else {
            "case_id": "same-case",
            "source": "ai",
            "assessor": "synthetic-assessor",
            "unsupported_additional_assertions": 0,
        }
    )
    with pytest.raises(ValueError, match="duplicate_semantic_assessment_case"):
        summarize_coverage_comparison([], [], semantic_assessments=[first, second])


@pytest.mark.parametrize(
    "trace",
    [
        False,
        {},
        {"tool_calls": True, "model_calls": 1, "latency_ms": 1.0},
        {"tool_calls": 1, "model_calls": 1, "latency_ms": float("nan")},
    ],
)
def test_invalid_cost_trace_cannot_be_misreported_as_missing_or_zero_cost(trace):
    worker = {
        "worker_task_id": "synthetic-worker",
        "paper_id": PAPER,
        "requirements": [{"requirement_id": f"req-{PAPER}-0"}],
        "trace": trace,
    }
    coverage = resolution() | {"complete": True}
    with pytest.raises((TypeError, ValueError), match="invalid_worker_cost"):
        summarize_coverage_comparison([worker], [coverage])


def test_none_cost_trace_explicitly_reports_missing_record_and_zero_cost_denominator():
    worker = {
        "worker_task_id": "synthetic-worker",
        "paper_id": PAPER,
        "requirements": [{"requirement_id": f"req-{PAPER}-0"}],
        "trace": None,
    }
    coverage = resolution() | {"complete": True}
    result = summarize_coverage_comparison([worker], [coverage])
    assert result["coverage_scope"] == "authoritative_worker_requirements"
    assert result["authoritative_requirement_count"] == 1
    assert result["complete_requirement_rate"]["denominator"] == 1
    assert result["worker_cost"]["missing_cost_records"] == 1
    assert result["worker_cost"]["denominator"] == 0
    assert (
        result["worker_cost"]["model_calls"] == result["worker_cost"]["tool_calls"] == 0
    )
    assert all(
        metric["value"] is None for metric in result["semantic_quality"].values()
    )
