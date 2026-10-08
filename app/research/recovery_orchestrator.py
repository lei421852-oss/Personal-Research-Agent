"""一次恢复轮次，预分配全任务预算，复用现有 Worker Pool 和审核运行时。"""

import asyncio
from collections import defaultdict
from time import perf_counter

from app.research.orchestrator import AsyncResearchOrchestrator
from app.research.recovery_eligibility import (
    RecoveryEligibilityEvaluator,
    bind_initial,
    dump,
)
from app.research.recovery_merge import RecoveryResultMerger
from app.research.recovery_models import (
    RecoveryBudget,
    RecoveryConfig,
    RecoveryFeedback,
    RecoveryInputError,
    RecoveryMetrics,
    RecoveryOutcome,
    RecoveryTask,
    result_ref,
)
from app.research.requirements import stable_id
from app.research.synthesis_input import SynthesisInputBuilder


class RecoveryOrchestrator:
    def __init__(
        self,
        worker_factory,
        review_runtime,
        resolver,
        prepared_paper_ids,
        *,
        config=None,
        synthesis_runtime=None,
        clock=perf_counter,
    ):
        self.factory, self.review_runtime = worker_factory, review_runtime
        self.resolver = resolver
        self.gate = review_runtime.resource_gate
        if review_runtime.validator.resolver is not resolver:
            raise ValueError(
                "Recovery and Reviewer must use the same trusted evidence snapshot"
            )
        if synthesis_runtime is not None:
            if synthesis_runtime.input_builder.validator.resolver is not resolver:
                raise ValueError(
                    "Recovery and Synthesis must use the same evidence snapshot"
                )
            if (
                synthesis_runtime.agent.llm is not None
                and synthesis_runtime.agent.llm.resource_gate is not self.gate
            ):
                raise ValueError("Synthesis must share the research resource gate")
        self.config = config or RecoveryConfig()
        if (
            self.review_runtime.config.max_model_calls_per_review
            != self.config.max_model_calls_per_review
        ):
            raise ValueError("Recovery review budget differs from Reviewer runtime")
        self.evaluator = RecoveryEligibilityEvaluator(resolver, prepared_paper_ids)
        self.merger, self.builder = (
            RecoveryResultMerger(resolver),
            SynthesisInputBuilder(resolver),
        )
        self.synthesis_runtime, self.clock = synthesis_runtime, clock
        self.last_outcome = None
        self.pool = None
        self.workers = []
        self._consumed = False

    async def run(
        self,
        plan,
        initial_workers,
        initial_reviews,
        feedback,
        *,
        prior_usage,
        task_cancelled=False,
    ):
        if self._consumed:
            raise RecoveryInputError("recovery_round_already_used")
        self._consumed = True
        plan, bound, review_map = bind_initial(plan, initial_workers, initial_reviews)
        prior = RecoveryBudget.model_validate(dump(prior_usage))
        known_models = sum(
            w.trace.model_calls for w in bound.values() if w.trace
        ) + sum(
            r.trace.model_calls for records in review_map.values() for r in records if r
        )
        known_tools = sum(w.trace.tool_calls for w in bound.values() if w.trace)
        if (
            prior.model_calls < known_models
            or prior.tool_calls < known_tools
            or prior.model_calls > self.config.max_research_model_calls
            or prior.tool_calls > self.config.max_research_tool_calls
        ):
            raise RecoveryInputError("invalid_prior_research_budget")
        decisions = self.evaluator.evaluate(
            plan,
            initial_workers,
            initial_reviews,
            feedback,
            task_cancelled=task_cancelled,
        )
        initial_merged = self.merger.merge(plan, initial_workers, initial_reviews)
        initial_input = self.builder.build(
            plan, initial_merged.worker_results, initial_merged.review_results
        )
        may_have_facts = bool(initial_input.verified_claims) or any(
            d.eligible for d in decisions
        )
        synth_reserve = (
            self.synthesis_runtime.agent.config.max_model_calls
            if self.synthesis_runtime is not None
            and self.synthesis_runtime.agent.llm is not None
            and may_have_facts
            and not task_cancelled
            else 0
        )
        remaining_models = (
            self.config.max_research_model_calls - prior.model_calls - synth_reserve
        )
        remaining_tools = self.config.max_research_tool_calls - prior.tool_calls
        if remaining_models < 0:
            raise RecoveryInputError("insufficient_synthesis_budget")
        feedback_map = {}
        for raw in feedback:
            try:
                item = RecoveryFeedback.model_validate(dump(raw))
                feedback_map[item.requirement_id] = item
            except (ValueError, TypeError, AttributeError):
                continue
        grouped = defaultdict(list)
        for decision in decisions:
            if decision.eligible:
                grouped[decision.paper_id].append(decision.requirement_id)
        tasks = []
        reserved_models, reserved_tools = synth_reserve, 0
        for paper, targets in grouped.items():
            review_lease = len(targets) * self.config.max_model_calls_per_review
            if remaining_models <= review_lease:
                continue
            models = min(
                self.config.max_model_calls_per_worker, remaining_models - review_lease
            )
            tools = min(self.config.max_tool_calls_per_worker, remaining_tools)
            worker = bound[paper]
            reviews = [review_map[i][0] for i in targets]
            annotations = [feedback_map[i] for i in targets]
            tasks.append(
                RecoveryTask(
                    recovery_task_id=stable_id(
                        "recovery_",
                        paper,
                        *targets,
                        *[f.rejected_claim_id for f in annotations],
                        "1",
                    ),
                    paper_id=paper,
                    target_requirement_ids=targets,
                    reviewer_feedback=annotations,
                    prior_result_refs=[
                        result_ref("worker", worker),
                        *[result_ref("review", r) for r in reviews],
                    ],
                    prior_worker_result=worker,
                    prior_reviews=reviews,
                    remaining_budget=RecoveryBudget(
                        model_calls=models, tool_calls=tools
                    ),
                    review_model_calls_per_requirement=self.config.max_model_calls_per_review,
                )
            )
            remaining_models -= models + review_lease
            remaining_tools -= tools
            reserved_models += models + review_lease
            reserved_tools += tools
        by_paper = {t.paper_id: t for t in tasks}

        def factory(pending):
            worker = self.factory(by_paper[pending.paper_id])
            if (
                worker.llm.resource_gate is not self.gate
                or worker.recovery_context != by_paper[pending.paper_id]
                or worker.resolver is not self.resolver
            ):
                raise ValueError(
                    "Recovery Worker must use the shared gate and exact context"
                )
            self.workers.append(worker)
            return worker

        recovered_workers, recovered_reviews, report = [], [], None
        review_started, synthesis_started = False, False
        started = self.clock()
        cancelled = task_cancelled

        def snapshot():
            merged = self.merger.merge(
                plan,
                initial_workers,
                initial_reviews,
                tasks,
                recovered_workers,
                recovered_reviews,
            )
            source = self.builder.build(
                plan, merged.worker_results, merged.review_results
            )
            target_ids = {i for t in tasks for i in t.target_requirement_ids}
            passed = sum(
                c.status == "verified" and c.requirement_id in target_ids
                for c in source.cells
            )
            models = sum(
                w.trace.model_calls for w in recovered_workers if w.trace
            ) + sum(r.trace.model_calls for r in recovered_reviews)
            tools = sum(w.trace.tool_calls for w in recovered_workers if w.trace)
            if report is not None:
                models += report.metrics.synthesis_model_calls
            if models > reserved_models or tools > reserved_tools:
                raise RecoveryInputError("recovery_exceeded_reserved_budget")
            self.last_outcome = RecoveryOutcome(
                eligibility=decisions,
                tasks=tasks,
                merged=merged,
                synthesis_input=source,
                report=report,
                status="cancelled" if cancelled else "completed",
                metrics=RecoveryMetrics(
                    recovery_eligible_count=sum(d.eligible for d in decisions),
                    recovery_attempted_count=len(target_ids),
                    recovery_passed_count=passed,
                    recovery_failed_count=len(target_ids) - passed,
                    recovery_skipped_count=len(decisions) - len(target_ids),
                    recovery_conversion_rate=passed / len(target_ids)
                    if target_ids
                    else None,
                    additional_model_calls=models,
                    additional_tool_calls=tools,
                    recovery_latency_ms=max(0, (self.clock() - started) * 1000),
                    recovery_rounds_used=int(bool(tasks)),
                    prior_usage=prior,
                    reserved_budget=RecoveryBudget(
                        model_calls=reserved_models, tool_calls=reserved_tools
                    ),
                    research_usage=RecoveryBudget(
                        model_calls=prior.model_calls + models,
                        tool_calls=prior.tool_calls + tools,
                    ),
                ),
            )
            return self.last_outcome

        try:
            if tasks:
                self.pool = AsyncResearchOrchestrator(
                    factory, self.gate.config, self.gate
                )
                recovered_workers = await self.pool.run(
                    [t.worker_task() for t in tasks]
                )
                review_started = True
                recovered_reviews = await self.review_runtime.run(recovered_workers)
            outcome = snapshot()
            if self.synthesis_runtime is not None and not task_cancelled:
                synthesis_started = True
                report = await self.synthesis_runtime.run(
                    plan, outcome.merged.worker_results, outcome.merged.review_results
                )
                outcome = snapshot()
            return outcome
        except asyncio.CancelledError:
            cancelled = True
            if self.pool is not None:
                recovered_workers = self.pool.last_results
            recovered_reviews = (
                self.review_runtime.last_results if review_started else []
            )
            if self.synthesis_runtime is not None and synthesis_started:
                report = self.synthesis_runtime.last_report
            snapshot()
            raise

    async def drain(self):
        for worker in self.workers:
            await worker.drain()
        await self.review_runtime.drain()
        if self.synthesis_runtime is not None:
            await self.synthesis_runtime.drain()
