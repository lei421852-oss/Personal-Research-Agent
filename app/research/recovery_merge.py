"""历史独立保留，最终每需求只有一个活动结果；不把失败漂白为证据不足。"""

from collections import defaultdict
from datetime import UTC, datetime

from app.research.models import WorkerResult
from app.research.orchestrator import worker_result_from_task
from app.research.recovery_eligibility import bind_initial, bound_review, dump
from app.research.recovery_models import (
    RecoveryHistoryEntry,
    RecoveryMergeResult,
    RecoveryTask,
    result_ref,
)
from app.research.requirements import expand_requirements, group_worker_tasks, stable_id
from app.research.review_models import ReviewIssue, ReviewResult
from app.research.review_validation import DeterministicReviewValidator


def unavailable_review(worker, req, code):
    return ReviewResult(
        worker_task_id=worker.worker_task_id,
        requirement_id=req.requirement_id,
        paper_id=req.paper_id,
        status="not_reviewable",
        claim=req.claim,
        evidence_ids=req.evidence_ids,
        validation_status="rejected",
        reason_code="invalid_structure",
        issue=ReviewIssue(
            code=code,
            message="There is no unambiguous valid review for this exact final claim.",
        ),
    )


class RecoveryResultMerger:
    def __init__(self, resolver):
        self.resolver = resolver
        self.validator = DeterministicReviewValidator(resolver)

    def merge(
        self,
        plan,
        initial_workers,
        initial_reviews,
        tasks=(),
        recovery_workers=(),
        recovery_reviews=(),
    ):
        plan, originals, reviews = bind_initial(plan, initial_workers, initial_reviews)
        task_map, worker_map, review_map = {}, defaultdict(list), defaultdict(list)
        for raw in tasks:
            task = RecoveryTask.model_validate(dump(raw))
            if (
                task.paper_id in task_map
                or task.prior_worker_result.model_dump()
                != originals[task.paper_id].model_dump()
            ):
                raise ValueError(
                    "Recovery task does not bind the initial paper snapshot"
                )
            task_map[task.paper_id] = task
        for raw in recovery_workers:
            worker = WorkerResult.model_validate(dump(raw))
            worker_map[worker.paper_id].append(worker)
        for raw in recovery_reviews:
            try:
                review_map[dump(raw)["requirement_id"]].append(
                    ReviewResult.model_validate(dump(raw))
                )
            except (ValueError, TypeError, AttributeError):
                review_map[dump(raw)["requirement_id"]].append(None)

        history = []
        for attempt, kind, records in [
            (0, "worker", initial_workers),
            (0, "review", initial_reviews),
            (1, "worker", recovery_workers),
            (1, "review", recovery_reviews),
        ]:
            for record in records:
                checked = (
                    WorkerResult if kind == "worker" else ReviewResult
                ).model_validate(dump(record))
                ref = result_ref(kind, checked)
                timestamp = datetime.now(UTC).isoformat()
                history.append(
                    RecoveryHistoryEntry(
                        record_id=stable_id(
                            "history_", ref, str(attempt), str(len(history)), timestamp
                        ),
                        result_ref=ref,
                        kind=kind,
                        attempt_index=attempt,
                        recorded_at=timestamp,
                        status=checked.status.value,
                        worker_result=checked if kind == "worker" else None,
                        review_result=checked if kind == "review" else None,
                    )
                )

        finals, final_reviews = [], []
        pending_tasks = {
            t.paper_id: t for t in group_worker_tasks(expand_requirements(plan))
        }
        for paper in plan.paper_ids:
            initial = originals[paper]
            items, observed = [], list(initial.observed_evidence_ids)
            task = task_map.get(paper)
            recovered = worker_map[paper][0] if len(worker_map[paper]) == 1 else None
            valid_scope = False
            if task and recovered:
                expected = {
                    r.requirement_id: r for r in task.worker_task().requirements
                }
                valid_scope = recovered.worker_task_id == initial.worker_task_id and {
                    r.requirement_id for r in recovered.requirements
                } == set(expected)
                for req in recovered.requirements:
                    original = expected.get(req.requirement_id)
                    valid_scope = (
                        valid_scope
                        and original is not None
                        and all(
                            getattr(req, f) == getattr(original, f)
                            for f in [
                                "paper_id",
                                "dimension_id",
                                "question",
                                "required",
                            ]
                        )
                    )
                try:
                    valid_scope = valid_scope and all(
                        self.resolver.resolve(i).paper_id == paper
                        for i in recovered.observed_evidence_ids
                    )
                except (ValueError, KeyError):
                    valid_scope = False
            for req in initial.requirements:
                initial_review = (
                    bound_review(
                        self.validator, initial, req, reviews[req.requirement_id]
                    )
                    if req.status == "supported"
                    else None
                )
                selected = req
                active = initial_review
                # 合法初始通过项优先且不可修改，即使恢复返回了越界结果。
                approved = (
                    initial_review is not None and initial_review.status == "passed"
                )
                if (
                    not approved
                    and task
                    and req.requirement_id in task.target_requirement_ids
                ):
                    bound_prior = next(
                        r
                        for r in task.prior_reviews
                        if r.requirement_id == req.requirement_id
                    )
                    if (
                        initial_review is None
                        or initial_review.model_dump() != bound_prior.model_dump()
                    ):
                        active = unavailable_review(
                            initial, req, "invalid_recovery_source"
                        )
                    elif not valid_scope:
                        active = unavailable_review(
                            initial, req, "invalid_recovery_result"
                        )
                    else:
                        replacement = next(
                            r
                            for r in recovered.requirements
                            if r.requirement_id == req.requirement_id
                        )
                        if replacement.status == "supported":
                            selected = replacement
                            active = bound_review(
                                self.validator,
                                recovered,
                                replacement,
                                review_map[req.requirement_id],
                            )
                            if replacement.claim == req.claim and set(
                                replacement.evidence_ids
                            ) == set(req.evidence_ids):
                                active = None
                            if active is None:
                                active = unavailable_review(
                                    recovered,
                                    replacement,
                                    "unavailable_recovery_review",
                                )
                            observed = list(
                                dict.fromkeys(
                                    [*observed, *recovered.observed_evidence_ids]
                                )
                            )
                        elif replacement.status in ("execution_failed", "cancelled"):
                            selected, active = replacement, None
                        # insufficient 只作为恢复历史，最终保留原审核拒绝，不改写成普通不足。
                items.append(selected)
                if selected.status == "supported":
                    final_reviews.append(
                        active
                        or unavailable_review(
                            initial, selected, "conflicting_or_missing_initial_review"
                        )
                    )
                else:
                    final_reviews.append(
                        ReviewResult(
                            worker_task_id=initial.worker_task_id,
                            requirement_id=selected.requirement_id,
                            paper_id=paper,
                            status="not_reviewable",
                            validation_status="passed"
                            if selected.status == "insufficient_evidence"
                            else "skipped",
                            skip_reason=selected.status.value,
                        )
                    )
            draft = worker_result_from_task(
                pending_tasks[paper],
                current_requirements=items,
                attempts=initial.attempts + int(task is not None),
            )
            finals.append(
                WorkerResult.model_validate(
                    draft.model_dump() | {"observed_evidence_ids": observed}
                )
            )
        return RecoveryMergeResult(
            worker_results=finals, review_results=final_reviews, history=history
        )
