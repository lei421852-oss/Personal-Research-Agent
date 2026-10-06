"""不按关键词判分，也不把执行 completed 自动当成答案正确。"""


def initial_task_success(case, status, answer):
    if status == "unsupported":
        return None, "pending_review"
    if not answer or status not in {"completed", "insufficient_evidence"}:
        return False, "automatic_failure"
    if (
        status == "insufficient_evidence"
        and case.expected_behavior != "insufficient_evidence"
    ):
        return False, "automatic_failure"
    return None, "pending_review"


def apply_review(run, review):
    if review.run_id != run.run_id:
        raise ValueError("Review belongs to a different run")
    if review.task_success is True and not run.answer:
        raise ValueError("A run without a final answer cannot be task-successful")
    if review.citation_correctness is not None and not run.citations:
        raise ValueError("Semantic citation score is not applicable without citations")
    data = run.model_dump()
    if review.task_success is not None:
        data.update(
            task_success=review.task_success, task_success_source="human_review"
        )
    if review.citation_correctness is not None:
        data.update(
            citation_correctness=review.citation_correctness,
            citation_correctness_source="human_review",
        )
    return type(run).model_validate(data)
