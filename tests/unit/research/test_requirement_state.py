"""候选证据关联是调查进度，不自动替模型决定某个需求已获支持。"""

import asyncio
import json

import pytest
from test_paper_worker import (
    EV,
    FIGURE,
    FakeSearch,
    Resolver,
    action,
    last_context,
    multimodal_subject,
    resolve,
    search,
    subject,
    task,
)
from test_worker_protocol_recovery import multiple

from app.tools.observations import ObservationIssue, ToolObservation

REQ0 = "req-paper-local-0"
REQ1 = "req-paper-local-1"
EV2 = EV.model_copy(
    update={"evidence_id": "ev-second", "text": "Another prepared passage."}
)


def associate(response, identities):
    response = response.model_copy(deep=True)
    response.tool_calls[0].arguments["requirement_ids"] = identities
    return response


def context_at(client, index):
    return json.loads(client.calls[index]["messages"][-1]["content"])


def states(context):
    return {item["requirement_id"]: item for item in context["requirement_states"]}


def found(*evidence):
    return ToolObservation(
        status="success",
        tool_name="search_text",
        summary="Prepared passages found.",
        payload={
            "evidence": [
                item.model_dump() | {"retrieval_score": 1.0} for item in evidence
            ]
        },
    )


def insufficient_batch():
    return action(
        "resolve_requirements",
        resolutions=[
            resolve(i, status="insufficient_evidence").tool_calls[0].arguments
            for i in range(2)
        ],
    )


def test_targeted_candidates_are_associated_and_local_argument_is_not_a_tool_field():
    worker, client, tool = subject([associate(search(), [REQ0]), insufficient_batch()])
    original_schema = worker.tools.registry.get("search_text").schema()
    assert "requirement_ids" not in original_schema["input_schema"]["properties"]
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed" and len(tool.calls) == 1
    progress = states(last_context(client))
    assert progress[REQ0]["candidate_evidence_ids"] == [EV.evidence_id]
    assert (
        progress[REQ0]["search_attempts"]
        == progress[REQ0]["last_new_evidence_count"]
        == 1
    )
    assert progress[REQ1]["candidate_evidence_ids"] == []
    assert (
        progress[REQ1]["search_attempts"]
        == progress[REQ1]["last_new_evidence_count"]
        == 0
    )
    assert last_context(client)["last_new_evidence_count"] == 1
    assert "requirement_ids" not in tool.calls[0].model_dump()
    assert worker.tools.registry.get("search_text").schema() == original_schema
    policy_schema = next(
        s for s in client.calls[0]["tools"] if s["name"] == "search_text"
    )
    assert "requirement_ids" in policy_schema["input_schema"]["properties"]


def test_candidate_evidence_and_high_score_never_automatically_support_a_requirement():
    worker, client, _ = subject([associate(search(), [REQ0]), insufficient_batch()])
    result = asyncio.run(worker.run(task(2)))
    context = last_context(client)
    assert all(item["status"] == "pending" for item in context["requirement_states"])
    assert context["resolved_requirements"] == []
    assert {item["requirement_id"] for item in context["unresolved_requirements"]} == {
        REQ0,
        REQ1,
    }
    assert result.status == "completed"
    assert all(
        r.status == "insufficient_evidence" and r.claim is None and r.evidence_ids == []
        for r in result.requirements
    )


def test_missing_association_preserves_shared_investigation_compatibility():
    worker, client, tool = subject([search(), resolve(0), resolve(1)])
    result = asyncio.run(worker.run(task(2)))
    first_observation_context = context_at(client, 1)
    progress = states(first_observation_context)
    assert all(
        item["candidate_evidence_ids"] == [EV.evidence_id] for item in progress.values()
    )
    assert all(item["search_attempts"] == 1 for item in progress.values())
    assert all(item["status"] == "pending" for item in progress.values())
    assert result.status == "completed" and len(tool.calls) == 1
    assert all(r.evidence_ids == [EV.evidence_id] for r in result.requirements)


def test_same_evidence_returned_twice_is_deduplicated_and_last_new_count_resets():
    worker, client, tool = subject(
        [
            associate(search("First perspective"), [REQ0]),
            associate(search("Second perspective"), [REQ0]),
            insufficient_batch(),
        ],
        tool=FakeSearch([found(EV, EV), found(EV)]),
    )
    result = asyncio.run(worker.run(task(2)))
    first = states(context_at(client, 1))[REQ0]
    second = states(last_context(client))[REQ0]
    assert (
        first["candidate_evidence_ids"]
        == second["candidate_evidence_ids"]
        == [EV.evidence_id]
    )
    assert (
        first["last_new_evidence_count"] == 1 and second["last_new_evidence_count"] == 0
    )
    assert second["search_attempts"] == len(tool.calls) == 2
    assert last_context(client)["last_new_evidence_count"] == 0
    assert result.status == "completed" and len(worker.state.collected_evidence) == 1


def test_existing_global_evidence_can_be_new_for_another_requirement():
    worker, client, _ = subject(
        [
            associate(search("Investigate first requirement"), [REQ0]),
            associate(search("Investigate second requirement"), [REQ1]),
            insufficient_batch(),
        ]
    )
    result = asyncio.run(worker.run(task(2)))
    context = last_context(client)
    progress = states(context)
    assert (
        progress[REQ0]["candidate_evidence_ids"]
        == progress[REQ1]["candidate_evidence_ids"]
        == [EV.evidence_id]
    )
    assert progress[REQ1]["last_new_evidence_count"] == 1
    assert context["last_new_evidence_count"] == 0
    assert progress[REQ0]["search_attempts"] == progress[REQ1]["search_attempts"] == 1
    assert result.status == "completed"


def test_multiple_new_evidence_ids_are_accumulated_without_duplicates():
    worker, client, _ = subject(
        [
            associate(search("First passage"), [REQ0]),
            associate(search("Additional passages"), [REQ0]),
            insufficient_batch(),
        ],
        tool=FakeSearch([found(EV), found(EV, EV2, EV2)]),
        resolver=Resolver((EV, EV2)),
    )
    result = asyncio.run(worker.run(task(2)))
    context = last_context(client)
    progress = states(context)[REQ0]
    assert set(progress["candidate_evidence_ids"]) == {EV.evidence_id, EV2.evidence_id}
    assert len(progress["candidate_evidence_ids"]) == 2
    assert (
        progress["last_new_evidence_count"] == context["last_new_evidence_count"] == 1
    )
    assert result.status == "completed"


def test_empty_search_counts_one_logical_attempt_and_zero_new_candidates():
    empty = ToolObservation(
        status="empty",
        tool_name="search_text",
        summary="Search returned no prepared passages.",
        payload={"evidence": []},
        issue=ObservationIssue(code="empty_result", retryable=False),
    )
    worker, client, tool = subject(
        [associate(search(), [REQ0]), insufficient_batch()], tool=FakeSearch([empty])
    )
    result = asyncio.run(worker.run(task(2)))
    context = last_context(client)
    progress = states(context)[REQ0]
    assert progress["search_attempts"] == len(tool.calls) == 1
    assert progress["candidate_evidence_ids"] == []
    assert (
        progress["last_new_evidence_count"] == context["last_new_evidence_count"] == 0
    )
    assert result.status == "completed" and result.trace.retry_count == 0


def test_provider_retries_are_not_multiple_requirement_search_attempts():
    worker, _, tool = subject(
        [associate(search(), [REQ0])],
        tool=FakeSearch([TimeoutError(), TimeoutError(), TimeoutError()]),
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "failed"
    assert len(tool.calls) == result.trace.tool_calls == 3
    assert result.trace.retry_count == 2
    assert worker.state.requirement_research[REQ0].search_attempts == 1
    assert worker.state.requirement_research[REQ1].search_attempts == 0
    assert worker.state.requirement_research[REQ0].candidate_evidence_ids == []


@pytest.mark.parametrize(
    "identities", [[], [REQ0, REQ0], ["req-other-worker-0"], REQ0, None]
)
def test_invalid_association_is_recoverable_and_never_dispatched(identities):
    worker, client, tool = subject(
        [associate(search(), identities), insufficient_batch()]
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed" and not tool.calls
    context = last_context(client)
    assert "invalid_arguments" in json.dumps(context["recent_observations"])
    assert all(
        item["search_attempts"] == 0 and item["candidate_evidence_ids"] == []
        for item in states(context).values()
    )
    assert result.trace.tool_calls == 0


def test_paper_scope_validation_happens_before_recording_a_search_attempt():
    worker, client, tool = subject(
        [
            associate(search(paper_id="another-paper"), [REQ0]),
            insufficient_batch(),
        ]
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed" and not tool.calls
    assert states(last_context(client))[REQ0]["search_attempts"] == 0


def test_resolved_and_unresolved_requirements_are_separate_and_default_scope_skips_resolved():
    worker, client, tool = subject(
        [
            associate(search("First requirement"), [REQ0]),
            resolve(0),
            search("Remaining investigation"),
            resolve(1, evidence_ids=[EV2.evidence_id]),
        ],
        tool=FakeSearch([found(EV), found(EV2)]),
        resolver=Resolver((EV, EV2)),
    )
    result = asyncio.run(worker.run(task(2)))
    context = last_context(client)
    progress = states(context)
    assert {item["requirement_id"] for item in context["resolved_requirements"]} == {
        REQ0
    }
    assert {item["requirement_id"] for item in context["unresolved_requirements"]} == {
        REQ1
    }
    assert (
        progress[REQ0]["status"] == "supported"
        and progress[REQ1]["status"] == "pending"
    )
    assert progress[REQ0]["candidate_evidence_ids"] == [EV.evidence_id]
    assert progress[REQ1]["candidate_evidence_ids"] == [EV2.evidence_id]
    assert progress[REQ0]["search_attempts"] == progress[REQ1]["search_attempts"] == 1
    assert result.status == "completed" and len(tool.calls) == 2


def test_explicit_target_cannot_reinvestigate_an_already_resolved_requirement():
    worker, client, tool = subject(
        [
            associate(search(), [REQ0]),
            resolve(0),
            associate(search("Invalid resolved target"), [REQ0]),
            resolve(1, status="insufficient_evidence"),
        ]
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed" and len(tool.calls) == 1
    context = last_context(client)
    assert "invalid_arguments" in json.dumps(context["recent_observations"])
    assert states(context)[REQ0]["search_attempts"] == 1
    assert states(context)[REQ1]["search_attempts"] == 0


def test_duplicate_action_does_not_increment_requirement_search_attempts():
    worker, client, tool = subject(
        [associate(search(), [REQ0]), associate(search(), [REQ0]), insufficient_batch()]
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed" and len(tool.calls) == 1
    context = last_context(client)
    assert states(context)[REQ0]["search_attempts"] == 1
    assert "redundant_action" in json.dumps(context["recent_observations"])


def test_corrupted_returned_evidence_never_becomes_a_candidate():
    unknown = EV.model_copy(update={"evidence_id": "ev-outside-corpus"})
    worker, _, _ = subject(
        [associate(search(), [REQ0])], tool=FakeSearch([found(unknown)])
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "failed"
    assert all(r.status == "execution_failed" for r in result.requirements)
    assert worker.state.requirement_research[REQ0].candidate_evidence_ids == []
    assert worker.state.collected_evidence == {}


def test_corrupted_result_clears_new_counts_and_preserves_existing_candidates():
    unknown = EV.model_copy(update={"evidence_id": "ev-outside-corpus"})
    worker, _, _ = subject(
        [
            associate(search("First valid search"), [REQ0]),
            associate(search("Corrupted later result"), [REQ0]),
        ],
        tool=FakeSearch([found(EV), found(unknown)]),
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "failed"
    progress = worker.state.requirement_research[REQ0]
    assert progress.candidate_evidence_ids == [EV.evidence_id]
    assert progress.search_attempts == 2
    assert progress.last_new_evidence_count == worker.state.last_new_evidence_count == 0
    assert list(worker.state.collected_evidence) == [EV.evidence_id]


def test_figure_lookup_associates_candidates_without_counting_a_text_search():
    worker, client, analysis = multimodal_subject()
    client.responses[:] = [
        associate(
            action("find_figure", paper_id=FIGURE.paper_id, figure_id=FIGURE.figure_id),
            [REQ0],
        ),
        insufficient_batch(),
    ]
    result = asyncio.run(worker.run(task(2)))
    context = last_context(client)
    progress = states(context)
    assert progress[REQ0]["candidate_evidence_ids"] == [FIGURE.evidence_id]
    assert progress[REQ1]["candidate_evidence_ids"] == []
    assert all(item["search_attempts"] == 0 for item in progress.values())
    assert (
        progress[REQ0]["last_new_evidence_count"]
        == context["last_new_evidence_count"]
        == 1
    )
    assert result.status == "completed" and analysis.calls == 0


def test_protocol_recovery_preserves_requirement_targeting_and_has_no_rejected_side_effects():
    rejected = multiple(
        associate(search("Rejected first"), [REQ0]),
        associate(search("Rejected second"), [REQ1]),
    )
    batch = action(
        "resolve_requirements",
        resolutions=[
            resolve(0, status="insufficient_evidence").tool_calls[0].arguments,
            resolve(1).tool_calls[0].arguments,
        ],
    )
    worker, client, tool = subject(
        [rejected, associate(search("Corrected single"), [REQ1]), batch]
    )
    result = asyncio.run(worker.run(task(2)))
    after_rejection = states(context_at(client, 1))
    assert all(
        item["search_attempts"] == 0 and item["candidate_evidence_ids"] == []
        for item in after_rejection.values()
    )
    progress = states(last_context(client))
    assert progress[REQ0]["candidate_evidence_ids"] == []
    assert progress[REQ1]["candidate_evidence_ids"] == [EV.evidence_id]
    assert result.status == "completed" and len(tool.calls) == 1
    assert result.trace.actions[0]["execution_status"] == "not_executed"
    assert result.trace.actions[1]["protocol_recovery"] is True
