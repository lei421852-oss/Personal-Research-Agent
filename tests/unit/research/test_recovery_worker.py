"""同一个 Worker 控制器以窄目标和已核验旧证据执行恢复。"""

import asyncio
import json

import pytest
from test_paper_worker import Resolver, action, subject
from test_recovery_eligibility import recovery_task

from app.tools.observations import ToolObservation


def resolved(
    task,
    *,
    status="supported",
    claim="Corrected narrow supported claim.",
    evidence_ids=None,
):
    original = next(
        r
        for r in task.prior_worker_result.requirements
        if r.requirement_id == task.target_requirement_ids[0]
    )
    return action(
        "resolve_requirement",
        requirement_id=original.requirement_id,
        status=status,
        claim=claim if status == "supported" else None,
        evidence_ids=(evidence_ids or original.evidence_ids)
        if status == "supported"
        else [],
    )


def worker_subject(responses, task, resolver, **kwargs):
    worker, client, tool = subject(
        responses, resolver=Resolver(tuple(resolver.items.values())), **kwargs
    )
    worker.recovery_context = task
    return worker, client, tool


def test_claim_narrowing_uses_prior_observation_without_retrieval():
    task, _, _, _, resolver, _ = recovery_task(tools=0)
    worker, client, tool = worker_subject([resolved(task)], task, resolver)
    result = asyncio.run(worker.run(task.worker_task()))
    assert (
        result.status == "completed"
        and result.requirements[0].claim == "Corrected narrow supported claim."
    )
    assert not tool.calls and result.trace.tool_calls == 0
    assert result.observed_evidence_ids == task.reviewer_feedback[0].prior_evidence_ids
    context = json.loads(client.calls[0]["messages"][-1]["content"])
    assert context["recovery"]["target_requirement_ids"] == task.target_requirement_ids
    assert context["remaining_budget"]["tool_calls"] == 0
    assert context["collected_evidence"][0]["text"]


def test_approved_requirements_are_absent_and_cannot_be_modified():
    task, _, workers, _, resolver, _ = recovery_task()
    approved = workers[0].requirements[1]
    illegal = action(
        "resolve_requirement",
        requirement_id=approved.requirement_id,
        status="supported",
        claim="Attempted overwrite",
        evidence_ids=approved.evidence_ids,
    )
    worker, client, _ = worker_subject([illegal, resolved(task)], task, resolver)
    result = asyncio.run(worker.run(task.worker_task()))
    assert (
        len(result.requirements) == 1
        and result.requirements[0].requirement_id != approved.requirement_id
    )
    assert worker.state.actions[0]["issue_code"] == "invalid_arguments"
    assert (
        json.loads(client.calls[1]["messages"][-1]["content"])["recent_observations"][
            -1
        ]["status"]
        == "error"
    )


def test_same_rejected_claim_with_same_evidence_cannot_be_directly_restored():
    task, _, workers, _, resolver, _ = recovery_task()
    old = workers[0].requirements[0]
    worker, _, _ = worker_subject(
        [resolved(task, claim=old.claim), resolved(task)], task, resolver
    )
    result = asyncio.run(worker.run(task.worker_task()))
    assert result.requirements[0].claim != old.claim
    assert result.trace.actions[0]["issue_code"] == "invalid_arguments"


def test_targeted_search_can_add_observed_evidence_for_new_claim():
    task, _, _, _, resolver, _ = recovery_task()
    old = resolver.resolve(task.reviewer_feedback[0].prior_evidence_ids[0])
    added = old.model_copy(
        update={
            "evidence_id": "ev-new-recovery",
            "text": "Additional support discovered during recovery.",
        }
    )
    resolver.items[added.evidence_id] = added
    from test_paper_worker import FakeSearch

    tool = FakeSearch(
        [
            ToolObservation(
                status="success",
                tool_name="search_text",
                summary="New prepared evidence.",
                payload={"evidence": [added.model_dump() | {"retrieval_score": 1.0}]},
            )
        ]
    )
    search = action(
        "search_text",
        query="Investigate missing claim detail",
        paper_ids=[task.paper_id],
        top_k=1,
    )
    worker, _, tool = worker_subject(
        [search, resolved(task, evidence_ids=[added.evidence_id])],
        task,
        resolver,
        tool=tool,
    )
    result = asyncio.run(worker.run(task.worker_task()))
    assert result.requirements[0].evidence_ids == [added.evidence_id]
    assert added.evidence_id in result.observed_evidence_ids and len(tool.calls) == 1


@pytest.mark.parametrize("identity", ["ev-nonexistent", "ev-paper-b-architecture"])
def test_unknown_or_wrong_paper_prior_evidence_fails_before_llm(identity):
    task, _, _, _, resolver, _ = recovery_task()
    # 构造被篡改实例，Worker须重新校验，而不是信任类型。
    bad = task.model_copy(
        update={
            "reviewer_feedback": [
                task.reviewer_feedback[0].model_copy(
                    update={"prior_evidence_ids": [identity]}
                )
            ]
        }
    )
    worker, client, _ = worker_subject([resolved(task)], bad, resolver)
    result = asyncio.run(worker.run(task.worker_task()))
    assert result.status == "failed" and result.error.code == "invalid_recovery_context"
    assert not client.calls


def test_tool_and_model_caps_are_enforced_in_recovery_mode():
    task, _, _, _, resolver, _ = recovery_task(tools=0, models=1)
    search = action(
        "search_text", query="Need more evidence", paper_ids=[task.paper_id], top_k=1
    )
    worker, client, tool = worker_subject([search, resolved(task)], task, resolver)
    result = asyncio.run(worker.run(task.worker_task()))
    assert result.status == "failed" and result.error.code == "budget_exhausted"
    assert len(client.calls) == 1 and not tool.calls


def test_protocol_self_correction_keeps_recovery_evidence_and_does_not_execute_multi_action():
    from app.llm.models import LLMResponse

    task, _, _, _, resolver, _ = recovery_task(tools=0)
    valid = resolved(task)
    invalid = LLMResponse(tool_calls=valid.tool_calls * 2)
    worker, client, tool = worker_subject([invalid, valid], task, resolver)
    result = asyncio.run(worker.run(task.worker_task()))
    assert result.status == "completed" and result.trace.protocol_corrections == 1
    assert result.trace.model_calls == 2 and not tool.calls
    context = json.loads(client.calls[1]["messages"][-1]["content"])
    assert context["collected_evidence"] and context["recovery"]["reviewer_feedback"]
