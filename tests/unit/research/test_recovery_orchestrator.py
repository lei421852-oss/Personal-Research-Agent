"""恢复生命周期、预算租约和既有池失败隔离的集成测试。"""

import asyncio
import json

import pytest
from test_paper_worker import Resolver, action, subject
from test_recovery_eligibility import initial
from test_recovery_worker import resolved
from test_reviewer import proposal as review_proposal
from test_synthesis_agent import HTTPFailure, fail, response
from test_synthesis_input import proposal

from app.llm.fake import ScriptedLLMClient
from app.research.async_tools import AsyncLLMAdapter
from app.research.recovery_models import (
    RecoveryBudget,
    RecoveryConfig,
    RecoveryInputError,
)
from app.research.recovery_orchestrator import RecoveryOrchestrator
from app.research.retry import RetryPolicy
from app.research.review_runtime import EvidenceReviewRuntime
from app.research.review_validation import DeterministicReviewValidator
from app.research.reviewer import EvidenceReviewer
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.synthesis_agent import SynthesisAgent
from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_models import SynthesisInput
from app.research.synthesis_runtime import SynthesisRuntime


def runtime_subject(
    *,
    config=None,
    mode="passed",
    synthesis=False,
    rejected=None,
    worker_client=None,
    gate_config=None,
):
    plan, workers, reviews, corpus, feedback = initial(rejected=rejected)
    resolver = Resolver(tuple(corpus.items.values()))
    gate = ResourceGate(gate_config)
    created = []

    async def no_sleep(delay):
        pass

    def reviewer_factory(task):
        verdict = (
            review_proposal()
            if mode != "rejected"
            else review_proposal("rejected", "evidence_does_not_support_claim")
        )
        return EvidenceReviewer(
            AsyncLLMAdapter(
                ScriptedLLMClient([verdict]),
                gate,
                retry_policy=RetryPolicy(2, sleeper=no_sleep),
            )
        )

    reviewer = EvidenceReviewRuntime(
        reviewer_factory, DeterministicReviewValidator(resolver), resource_gate=gate
    )

    def factory(task):
        if mode == "sibling_failure" and task.paper_id == "paper-a":
            raise RuntimeError("controlled factory failure")
        resolutions = [resolved(task)]
        if len(task.target_requirement_ids) > 1:
            prior = {r.requirement_id: r for r in task.prior_worker_result.requirements}
            resolutions = [
                action(
                    "resolve_requirements",
                    resolutions=[
                        {
                            "requirement_id": i,
                            "status": "supported",
                            "claim": "Narrowed " + i,
                            "evidence_ids": prior[i].evidence_ids,
                        }
                        for i in task.target_requirement_ids
                    ],
                )
            ]
        if mode == "transient":
            resolutions = [fail(HTTPFailure(429)), *resolutions]
        if mode == "permanent":
            resolutions = [fail(HTTPFailure(401))]
        worker, client, tool = subject(
            resolutions, resolver=resolver, gate=gate, client=worker_client
        )
        worker.recovery_context = task
        created.append((task, worker, client, tool))
        return worker

    synthesis_runtime = None
    if synthesis:

        def synthesize(messages, tools):
            source = SynthesisInput.model_validate(
                json.loads(messages[-1]["content"])["input"]
            )
            return response(proposal(source))

        synthesis_runtime = SynthesisRuntime(
            SynthesisInputBuilder(resolver),
            SynthesisAgent(AsyncLLMAdapter(ScriptedLLMClient([synthesize]), gate)),
        )
    runtime = RecoveryOrchestrator(
        factory,
        reviewer,
        resolver,
        plan.paper_ids,
        config=config,
        synthesis_runtime=synthesis_runtime,
    )
    return runtime, plan, workers, reviews, feedback, created


def execute(runtime, plan, workers, reviews, feedback, **kwargs):
    return asyncio.run(
        runtime.run(
            plan,
            workers,
            reviews,
            feedback,
            prior_usage=RecoveryBudget(model_calls=0, tool_calls=0),
            **kwargs,
        )
    )


def test_recovery_passed_flows_through_existing_synthesis_and_costs():
    runtime, plan, workers, reviews, feedback, created = runtime_subject(synthesis=True)
    result = execute(runtime, plan, workers, reviews, feedback)
    assert len(created) == 1 and result.metrics.recovery_rounds_used == 1
    assert (
        result.metrics.recovery_attempted_count
        == result.metrics.recovery_passed_count
        == 1
    )
    assert (
        result.metrics.additional_model_calls == 3
        and result.metrics.additional_tool_calls == 0
    )
    assert (
        result.report.status == "completed" and len(result.report.verified_claims) == 6
    )
    assert result.metrics.research_usage.model_calls == 3
    assert result.merged.worker_results[0].requirements[1] == workers[0].requirements[1]


def test_same_paper_targets_group_to_one_worker_and_two_reviews():
    args = runtime_subject(
        rejected={("paper-a", "architecture"), ("paper-a", "training")}
    )
    result = execute(*args[:5])
    assert len(result.tasks) == len(args[5]) == 1
    assert len(result.tasks[0].target_requirement_ids) == 2
    assert (
        result.metrics.recovery_passed_count == 2
        and result.metrics.additional_model_calls == 3
    )


def test_rejected_again_is_terminal_and_instance_cannot_run_second_round():
    runtime, plan, workers, reviews, feedback, created = runtime_subject(
        mode="rejected"
    )
    result = execute(runtime, plan, workers, reviews, feedback)
    assert result.metrics.recovery_failed_count == 1
    assert result.synthesis_input.cells[0].status == "review_rejected"
    assert len(created) == 1
    with pytest.raises(RecoveryInputError, match="recovery_round_already_used"):
        execute(runtime, plan, workers, reviews, feedback)


def test_low_global_budget_does_not_start_worker_or_review():
    args = runtime_subject(config=RecoveryConfig(max_research_model_calls=3))
    result = execute(*args[:5])
    assert not args[5] and not result.tasks
    assert (
        result.metrics.recovery_eligible_count == 1
        and result.metrics.recovery_attempted_count == 0
    )
    assert result.metrics.additional_model_calls == 0


@pytest.mark.parametrize(
    "mode,models,expected",
    [("transient", 3, "verified"), ("permanent", 1, "execution_failed")],
)
def test_provider_retry_reuses_existing_adapter_and_budget(mode, models, expected):
    args = runtime_subject(mode=mode)
    result = execute(*args[:5])
    assert result.metrics.additional_model_calls == models
    assert result.synthesis_input.cells[0].status == expected
    assert args[5][0][1].last_result.trace.retry_count == int(mode == "transient")


def test_sibling_worker_failure_preserves_other_recovery_and_original_approved():
    args = runtime_subject(
        mode="sibling_failure",
        rejected={("paper-a", "architecture"), ("paper-b", "architecture")},
    )
    result = execute(*args[:5])
    assert (
        result.metrics.recovery_attempted_count == 2
        and result.metrics.recovery_passed_count == 1
    )
    assert result.synthesis_input.cells[0].status == "execution_failed"
    assert result.synthesis_input.cells[2].status == "verified"
    assert result.synthesis_input.cells[1].status == "verified"


def test_original_task_cancelled_skips_all_models():
    args = runtime_subject()
    result = execute(*args[:5], task_cancelled=True)
    assert result.status == "cancelled" and not args[5]
    assert result.metrics.additional_model_calls == 0


def test_cancellation_propagates_with_original_approved_facts_retained():
    async def scenario():
        entered = asyncio.Event()

        class Client:
            async def complete(self, *, messages, tools):
                entered.set()
                await asyncio.Event().wait()

        args = runtime_subject(worker_client=Client())
        runtime, plan, workers, reviews, feedback = args[:5]
        running = asyncio.create_task(
            runtime.run(
                plan,
                workers,
                reviews,
                feedback,
                prior_usage=RecoveryBudget(model_calls=0, tool_calls=0),
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert runtime.last_outcome.status == "cancelled"
        assert runtime.last_outcome.synthesis_input.cells[0].status == "cancelled"
        assert len(runtime.last_outcome.synthesis_input.verified_claims) == 5
        assert runtime.last_outcome.metrics.additional_model_calls == 1

    asyncio.run(scenario())


def test_existing_worker_and_llm_semaphores_remain_bounded():
    args = runtime_subject(
        rejected={("paper-a", "architecture"), ("paper-b", "architecture")},
        gate_config=ResearchRuntimeConfig(
            max_concurrent_workers=1, max_concurrent_llm=1
        ),
    )
    result = execute(*args[:5])
    assert result.metrics.recovery_passed_count == 2
    assert args[0].pool.trace.peak_worker_concurrency == 1
    assert args[0].gate.peaks["llm"] == 1


def test_invalid_feedback_is_skipped_by_entire_runtime_not_raised():
    args = runtime_subject()
    invalid = args[4][0].model_copy(update={"explanation": " "})
    result = execute(*args[:4], [invalid])
    assert not args[5] and result.metrics.additional_model_calls == 0


def test_recovery_reviewer_cannot_use_a_different_trusted_index():
    args = runtime_subject()
    runtime = args[0]
    with pytest.raises(ValueError, match="same trusted evidence snapshot"):
        RecoveryOrchestrator(
            runtime.factory,
            runtime.review_runtime,
            Resolver(tuple(runtime.resolver.items.values())),
            args[1].paper_ids,
        )


def test_optional_synthesis_budget_is_reserved_before_recovery_workers():
    args = runtime_subject(
        config=RecoveryConfig(max_research_model_calls=7), synthesis=True
    )
    result = execute(*args[:5])
    assert not args[5] and result.metrics.recovery_attempted_count == 0
    assert result.metrics.additional_model_calls == 1
    assert result.metrics.reserved_budget.model_calls == 5
    assert result.synthesis_input.cells[0].status == "review_rejected"


def test_all_insufficient_with_exhausted_prior_budget_needs_no_provider():
    args = runtime_subject(synthesis=True)
    runtime, plan, workers, reviews, _feedback = args[:5]
    statuses = {
        (p, d): "insufficient_evidence"
        for p in plan.paper_ids
        for d in ["architecture", "training"]
    }
    from test_synthesis_input import bundle

    plan, workers, reviews, _ = bundle(statuses=statuses)
    result = asyncio.run(
        runtime.run(
            plan,
            workers,
            reviews,
            [],
            prior_usage=RecoveryBudget(model_calls=80, tool_calls=32),
        )
    )
    assert result.metrics.additional_model_calls == 0
    assert result.report.status == "insufficient_evidence" and not args[5]


def test_cancel_during_worker_does_not_reuse_previous_review_batch():
    async def scenario():
        entered = asyncio.Event()

        class Client:
            async def complete(self, *, messages, tools):
                entered.set()
                await asyncio.Event().wait()

        args = runtime_subject(worker_client=Client())
        runtime, plan, workers, reviews, feedback = args[:5]
        runtime.review_runtime.last_results = reviews
        running = asyncio.create_task(
            runtime.run(
                plan,
                workers,
                reviews,
                feedback,
                prior_usage=RecoveryBudget(model_calls=0, tool_calls=0),
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert runtime.last_outcome.metrics.additional_model_calls == 1
        assert not [
            h
            for h in runtime.last_outcome.merged.history
            if h.kind == "review" and h.attempt_index == 1
        ]

    asyncio.run(scenario())
