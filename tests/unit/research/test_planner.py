import json

import pytest
from pydantic import ValidationError

from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.papers.catalog import load_paper_catalog
from app.research.models import (
    EvidenceRequirement,
    RequirementStatus,
    ResearchDimension,
    ResearchPlan,
    WorkerTask,
)
from app.research.planner import ResearchPlanner, ResearchPlannerError
from app.research.requirements import (
    expand_requirements,
    group_worker_tasks,
    research_completion,
)

PAPERS = ["blip-2-2301.12597v1", "flamingo-2204.14198v1", "llava-2304.08485v1"]
QUESTION = (
    "Compare how these models connect visual representations to language modeling."
)


def dimension(index=0):
    return {
        "dimension_id": f"research_axis_{index}",
        "name": f"Research axis {index}",
        "description": f"Investigate the user-requested research axis {index}.",
        "research_question": f"What information is needed to address research axis {index}?",
    }


def proposal(dimensions=None, **extra):
    return LLMResponse(
        tool_calls=[
            ToolCall(
                id="plan-1",
                name="submit_research_dimensions",
                arguments={
                    "dimensions": dimensions
                    if dimensions is not None
                    else [dimension()]
                }
                | extra,
            )
        ]
    )


def planner(responses, **options):
    llm = ScriptedLLMClient(responses)
    return ResearchPlanner(llm, load_paper_catalog(), **options), llm


def test_single_paper_single_dimension_pending_and_no_answers():
    dims = [
        {
            "dimension_id": "visual_language_connection",
            "name": "Visual-language connection",
            "description": "How visual and language representations interact.",
            "research_question": "How does the model connect visual representations to language modeling?",
        }
    ]
    subject, llm = planner([proposal(dims)])
    result = subject.prepare(QUESTION, PAPERS[:1])
    assert len(result.requirements) == len(result.worker_tasks) == 1
    requirement = result.requirements[0]
    assert requirement.question == dims[0]["research_question"]
    assert requirement.status == "pending" and requirement.required is True
    assert requirement.claim is None and requirement.evidence_ids == []
    assert result.plan.question == QUESTION and result.plan.paper_ids == PAPERS[:1]
    assert len(llm.calls) == result.trace.model_calls == 1
    assert not research_completion(result.requirements).research_complete
    assert "answer" not in WorkerTask.model_fields


def test_three_papers_five_dimensions_fifteen_requirements_three_workers():
    subject, _ = planner([proposal([dimension(i) for i in range(5)])])
    result = subject.prepare(QUESTION, PAPERS)
    assert len(result.requirements) == 15
    assert len(result.worker_tasks) == 3
    assert all(len(w.requirements) == 5 for w in result.worker_tasks)
    assert {(r.paper_id, r.dimension_id) for r in result.requirements} == {
        (p, d.dimension_id) for p in PAPERS for d in result.plan.dimensions
    }
    rebuilt = expand_requirements(
        ResearchPlan.model_validate_json(result.plan.model_dump_json())
    )
    assert [r.requirement_id for r in rebuilt] == [
        r.requirement_id for r in result.requirements
    ]
    assert len({r.requirement_id for r in rebuilt}) == 15
    assert group_worker_tasks(rebuilt) == result.worker_tasks
    assert result.trace.requirement_count == 15 and result.trace.worker_task_count == 3


def test_group_interleaved_requirements_still_one_worker_per_paper():
    plan = ResearchPlan(
        question=QUESTION,
        paper_ids=PAPERS[:2],
        dimensions=[ResearchDimension(**dimension(i)) for i in range(2)],
    )
    req = expand_requirements(plan)
    grouped = group_worker_tasks([req[0], req[2], req[1], req[3]])
    assert [w.paper_id for w in grouped] == PAPERS[:2]
    assert all(len(w.requirements) == 2 for w in grouped)
    assert all(r.paper_id == w.paper_id for w in grouped for r in w.requirements)
    with pytest.raises(ValueError):
        group_worker_tasks([req[0], req[0]])
    with pytest.raises(ValidationError):
        WorkerTask(worker_task_id="worker", paper_id=PAPERS[0], requirements=[req[2]])


@pytest.mark.parametrize(
    "dimensions",
    [
        [],
        [dimension(i) for i in range(9)],
        [dimension(), dimension()],
        [
            dimension(),
            dimension(1)
            | {"research_question": dimension()["research_question"].upper()},
        ],
        [{k: v for k, v in dimension().items() if k != "research_question"}],
        [dimension() | {"research_question": " "}],
        [dimension() | {"dimension_id": "bad-id"}],
        [dimension() | {"expected_answer": "This model uses a particular component."}],
    ],
)
def test_invalid_dimensions_are_rejected_without_silent_fix(dimensions):
    subject, llm = planner([proposal(dimensions)])
    with pytest.raises(ResearchPlannerError) as failure:
        subject.plan(QUESTION, PAPERS)
    assert failure.value.code == "invalid_plan"
    assert len(llm.calls) == 1
    assert subject.traces[-1].status == "error"


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(content="A free text plan is not a structured proposal."),
        LLMResponse(tool_calls=[ToolCall(id="x", name="finish", arguments={})]),
        LLMResponse(tool_calls=proposal().tool_calls * 2),
        proposal(expected_answers={PAPERS[0]: "A guessed paper fact"}),
    ],
)
def test_malformed_or_answer_bearing_output_has_explicit_error(response):
    subject, _ = planner([response])
    with pytest.raises(ResearchPlannerError, match="Planner must") as failure:
        subject.plan(QUESTION, PAPERS)
    assert failure.value.code == "invalid_plan"


@pytest.mark.parametrize("question", ["", " \t ", None, 3])
def test_empty_or_invalid_question_never_calls_llm(question):
    subject, llm = planner([])
    with pytest.raises(ResearchPlannerError) as failure:
        subject.plan(question, PAPERS)
    assert failure.value.code == "invalid_arguments"
    assert not llm.calls and subject.traces[-1].model_calls == 0


@pytest.mark.parametrize(
    "ids,code",
    [
        ([], "invalid_arguments"),
        (None, "invalid_arguments"),
        ([PAPERS[0], PAPERS[0]], "invalid_arguments"),
        (["BLIP-2"], "unknown_paper"),
        (["unknown-paper"], "unknown_paper"),
        (PAPERS[0], "invalid_arguments"),
    ],
)
def test_invalid_candidate_papers_never_calls_llm_or_maps_alias(ids, code):
    subject, llm = planner([])
    with pytest.raises(ResearchPlannerError) as failure:
        subject.plan(QUESTION, ids)
    assert failure.value.code == code
    assert not llm.calls


def test_llm_failure_not_retried_and_private_detail_not_in_trace():
    def fail(messages, tools):
        raise ConnectionError(
            "private-provider-detail /home/private/path sk-private-key"
        )

    subject, llm = planner([fail])
    with pytest.raises(ResearchPlannerError) as failure:
        subject.prepare(QUESTION, PAPERS)
    assert failure.value.code == "dependency_failure" and len(llm.calls) == 1
    assert subject.traces[-1].failure["phase"] == "planner_model"
    trace = subject.traces[-1].model_dump_json()
    for forbidden in [
        "private-provider-detail",
        "/home/",
        "sk-private-key",
        "Traceback",
    ]:
        assert forbidden not in trace


def test_schema_failure_reports_field_location_without_raw_output():
    bad = {k: v for k, v in dimension().items() if k != "research_question"}
    subject, _ = planner([proposal([bad])])
    with pytest.raises(ResearchPlannerError):
        subject.prepare(QUESTION, PAPERS)
    failure = subject.traces[-1].failure
    assert failure["phase"] == "planner_schema"
    assert failure["validation_errors"] == [
        {"field": ["dimensions", 0, "research_question"], "type": "missing"}
    ]
    assert dimension()["description"] not in json.dumps(failure)


def test_dimension_limit_configured_and_no_fixed_vocabulary():
    dynamic = dimension() | {"dimension_id": "custom_user_requested_axis"}
    subject, llm = planner([proposal([dynamic])], max_dimensions=2)
    result = subject.plan(QUESTION, PAPERS)
    assert result.dimensions[0].dimension_id == "custom_user_requested_axis"
    assert (
        llm.calls[0]["tools"][0]["input_schema"]["properties"]["dimensions"]["maxItems"]
        == 2
    )
    subject, _ = planner([proposal([dimension(i) for i in range(3)])], max_dimensions=2)
    with pytest.raises(ResearchPlannerError):
        subject.plan(QUESTION, PAPERS)
    for limit in [0, 9, True, 1.5]:
        with pytest.raises(ValueError):
            planner([], max_dimensions=limit)


def test_optional_compact_metadata_and_neutral_prompt_contract():
    catalog = load_paper_catalog()
    metadata = {"paper_id": PAPERS[0], "title": catalog.resolve(PAPERS[0]).title}
    subject, llm = planner([proposal()])
    result = subject.prepare(QUESTION, PAPERS[:1], compact_paper_metadata=[metadata])
    payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert payload == {
        "question": QUESTION,
        "candidate_paper_ids": PAPERS[:1],
        "compact_paper_metadata": [metadata],
    }
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "not to predict what the papers will say" in prompt
    assert "No answer assumptions" in prompt and "neutral research questions" in prompt
    assert "stay within its scope" in prompt
    assert (
        "do not list candidate" in prompt
        and "including names and descriptions" in prompt
    )
    assert "merely to provide helpful context" in prompt
    assert all(r.claim is None and not r.evidence_ids for r in result.requirements)
    assert (
        "expected_answer"
        not in llm.calls[0]["tools"][0]["input_schema"]["$defs"]["ResearchDimension"][
            "properties"
        ]
    )
    subject, llm = planner([])
    with pytest.raises(ResearchPlannerError):
        subject.plan(
            QUESTION,
            PAPERS[:1],
            compact_paper_metadata=[metadata | {"paper_id": PAPERS[1]}],
        )
    assert not llm.calls


def requirement(index=0, **changes):
    return EvidenceRequirement(
        requirement_id=f"req-{index}",
        paper_id=PAPERS[0],
        dimension_id=f"axis_{index}",
        question="What does the prepared evidence support?",
        **changes,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"status": "supported"},
        {"status": "supported", "claim": "A supported claim"},
        {"status": "supported", "evidence_ids": ["ev-test"]},
        {"status": "supported", "claim": " ", "evidence_ids": ["ev-test"]},
        {"status": "pending", "claim": "An assumption"},
        {"status": "pending", "evidence_ids": ["ev-test"]},
        {"status": "insufficient_evidence", "claim": "An unsupported claim"},
        {"status": "insufficient_evidence", "evidence_ids": ["ev-test"]},
        {"status": "execution_failed", "claim": "A claim after failure"},
        {"status": "cancelled", "evidence_ids": ["ev-test"]},
        {
            "status": "supported",
            "claim": "A claim",
            "evidence_ids": ["ev-test", "ev-test"],
        },
        {"required": "yes"},
    ],
)
def test_requirement_status_invariants(values):
    with pytest.raises(ValidationError):
        requirement(**values)


def test_complete_means_all_resolved_not_all_supported():
    supported = [
        requirement(
            i,
            status="supported",
            claim="Synthetic supported claim",
            evidence_ids=[f"ev-{i}"],
        )
        for i in range(8)
    ]
    insufficient = [
        requirement(i, status="insufficient_evidence") for i in range(8, 10)
    ]
    complete = research_completion(supported + insufficient)
    assert complete.resolution_rate == 1 and complete.coverage == 0.8
    assert complete.research_complete
    pending = research_completion(supported + insufficient[:1] + [requirement(9)])
    assert pending.resolution_rate == 0.9 and pending.coverage == 0.8
    assert not pending.research_complete


def test_failed_cancelled_and_empty_are_not_research_complete():
    for status in [RequirementStatus.EXECUTION_FAILED, RequirementStatus.CANCELLED]:
        completion = research_completion([requirement(status=status)])
        assert completion.resolution_rate == completion.coverage == 0
        assert not completion.research_complete
    assert not research_completion([]).research_complete
    with pytest.raises(ValueError):
        research_completion([requirement(), requirement()])


def test_trace_contains_execution_facts_not_model_body_or_prompt():
    response = proposal().model_copy(
        update={"content": "private hidden reasoning body"}
    )
    subject, _ = planner([response])
    result = subject.prepare(QUESTION, PAPERS)
    trace = result.trace
    assert trace.events == ["planner_started", "planner_completed"]
    assert trace.candidate_paper_count == 3 and trace.dimension_count == 1
    assert trace.requirement_count == 3 and trace.worker_task_count == 3
    assert trace.model_calls == 1 and trace.latency_ms >= 0
    serialized = result.model_dump_json()
    assert "private hidden reasoning body" not in serialized
    assert "Your job is to identify" not in serialized
