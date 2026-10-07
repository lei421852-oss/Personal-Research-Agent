"""单论文共享上下文、证据身份、硬预算与终态语义的伪策略回归。"""

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.evidence.models import FigureEvidence, NonEmpty, TextEvidence
from app.evidence.resolver import EvidenceResolutionError
from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.models import EvidenceRequirement, WorkerTask
from app.research.retry import RetryPolicy
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.worker import PaperResearchWorker
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.registry import ToolRegistry

PAPER = "paper-local"
EV = TextEvidence(
    evidence_id="ev-local",
    paper_id=PAPER,
    page=2,
    text="Prepared evidence supporting the paper-local research questions.",
)
FIGURE = FigureEvidence(
    evidence_id="ev-figure-local",
    paper_id=PAPER,
    page=2,
    text="Figure describing the local paper architecture.",
    figure_id="figure-1",
    caption="Architecture figure from prepared corpus.",
    asset_path="/tmp/private/figure.png",
)


def task(count=1, paper_id=PAPER):
    return WorkerTask(
        worker_task_id=f"worker-{paper_id}",
        paper_id=paper_id,
        requirements=[
            EvidenceRequirement(
                requirement_id=f"req-{paper_id}-{i}",
                paper_id=paper_id,
                dimension_id=f"axis_{i}",
                question=f"What does this paper report about axis {i}?",
            )
            for i in range(count)
        ],
    )


def action(name, **arguments):
    return LLMResponse(
        content="private hidden reasoning body",
        tool_calls=[ToolCall(id="call-test", name=name, arguments=arguments)],
    )


def search(query="Find evidence", paper_id=PAPER):
    return action("search_text", query=query, paper_ids=[paper_id], top_k=1)


def resolve(index=0, *, status="supported", paper_id=PAPER, evidence_ids=None):
    return action(
        "resolve_requirement",
        requirement_id=f"req-{paper_id}-{index}",
        status=status,
        claim="Supported synthetic paper-local claim"
        if status == "supported"
        else None,
        evidence_ids=[EV.evidence_id]
        if status == "supported" and evidence_ids is None
        else evidence_ids or [],
    )


class Resolver:
    def __init__(self, evidence=(EV,)):
        self.items = {e.evidence_id: e for e in evidence}
        self.paper_ids = list(dict.fromkeys([e.paper_id for e in evidence]))
        self.paper_catalog = [{"paper_id": p, "name": p} for p in self.paper_ids]

    def resolve(self, identity):
        if identity not in self.items:
            raise EvidenceResolutionError("Evidence is outside prepared corpus")
        return self.items[identity]

    def figure(self, identity):
        item = self.resolve(identity)
        if not isinstance(item, FigureEvidence):
            raise EvidenceResolutionError("Expected prepared figure evidence")
        return item

    def contexts(self, identities, paper_id):
        items = [self.resolve(i) for i in identities]
        if any(
            not isinstance(item, TextEvidence) or item.paper_id != paper_id
            for item in items
        ):
            raise EvidenceResolutionError("Expected same-paper text contexts")
        return items


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: NonEmpty
    paper_ids: list[NonEmpty] | None = None
    top_k: StrictInt = Field(default=1, ge=1)


class FakeSearch(Tool):
    name = "search_text"
    description = "Search prepared evidence in the requested paper scope."
    input_model = SearchInput

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])

    def execute(self, arguments):
        self.calls.append(arguments)
        if self.outcomes:
            result = self.outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return ToolObservation(
            status="success",
            tool_name=self.name,
            summary="One prepared text evidence.",
            payload={"evidence": [EV.model_dump() | {"retrieval_score": 0.9}]},
        )


def subject(
    responses, *, tool=None, resolver=None, config=None, client=None, gate=None
):
    config = config or ResearchRuntimeConfig()
    gate = gate or ResourceGate(config)
    client = client or ScriptedLLMClient(responses)
    tool = tool or FakeSearch()
    registry = ToolRegistry()
    registry.register(tool)

    async def no_sleep(delay):
        return None

    retry = RetryPolicy(
        max_retries=config.max_retries, sleeper=no_sleep, jitter=lambda: 0
    )
    llm = AsyncLLMAdapter(client, gate, retry_policy=retry)
    tools = AsyncToolExecutorAdapter(ToolExecutor(registry), gate, retry_policy=retry)
    worker = PaperResearchWorker(llm, tools, resolver or Resolver(), config)
    return worker, client, tool


def last_context(client):
    return json.loads(client.calls[-1]["messages"][-1]["content"])


def test_two_requirements_share_one_retrieval_and_observed_evidence():
    worker, client, tool = subject(
        [search(), resolve(0), resolve(1)],
        config=ResearchRuntimeConfig(max_tool_calls_per_worker=1),
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed"
    assert all(r.status == "supported" for r in result.requirements)
    assert all(r.evidence_ids == [EV.evidence_id] for r in result.requirements)
    assert len(tool.calls) == result.trace.tool_calls == 1
    assert len(client.calls) == result.trace.model_calls == 3
    context = last_context(client)
    assert context["paper_id"] == PAPER
    assert [r["evidence_id"] for r in context["collected_evidence"]] == [EV.evidence_id]
    assert "req-paper-local-0" in json.dumps(context["resolved_requirements"])
    assert len(context["unresolved_requirements"]) == 1
    assert context["remaining_budget"]["tool_calls"] == 0
    assert "private hidden reasoning body" not in result.model_dump_json()


def test_insufficient_resolution_completes_without_claim_or_fake_evidence():
    worker, _, tool = subject([resolve(status="insufficient_evidence")])
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and not tool.calls
    requirement = result.requirements[0]
    assert requirement.status == "insufficient_evidence"
    assert requirement.claim is None and requirement.evidence_ids == []


def test_empty_observation_can_change_query_then_resolve():
    empty = ToolObservation(
        status="empty",
        tool_name="search_text",
        summary="Search succeeded without matching evidence.",
        payload={"evidence": []},
        issue=ObservationIssue(code="empty_result", retryable=False),
    )
    tool = FakeSearch([empty])
    worker, client, _ = subject(
        [search(), search("A different information need"), resolve()], tool=tool
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert len(tool.calls) == 2
    assert "empty_result" in json.dumps(client.calls[1]["messages"])
    assert result.trace.retry_count == 0


def test_wrong_paper_scope_is_feedback_for_policy_and_never_executed():
    worker, client, tool = subject(
        [search(paper_id="another-paper"), search(), resolve()]
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and len(tool.calls) == 1
    assert tool.calls[0].paper_ids == [PAPER]
    assert "error" in json.dumps(client.calls[1]["messages"])
    assert result.trace.retry_count == 0


@pytest.mark.parametrize("evidence_ids", [["ev-not-observed"], ["ev-foreign"]])
def test_unobserved_or_foreign_evidence_cannot_support_a_requirement(evidence_ids):
    foreign = TextEvidence(
        evidence_id="ev-foreign",
        paper_id="paper-other",
        page=1,
        text="Foreign evidence.",
    )
    unobserved = EV.model_copy(update={"evidence_id": "ev-not-observed"})
    resolver = Resolver((EV, foreign, unobserved))
    worker, client, _ = subject(
        [resolve(evidence_ids=evidence_ids), resolve(status="insufficient_evidence")],
        resolver=resolver,
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert result.requirements[0].status == "insufficient_evidence"
    assert "error" in json.dumps(client.calls[1]["messages"])
    assert result.requirements[0].evidence_ids == []


def test_model_budget_exhaustion_turns_pending_into_execution_failed():
    worker, client, tool = subject(
        [search()], config=ResearchRuntimeConfig(max_model_calls_per_worker=1)
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "failed" and result.error.code == "budget_exhausted"
    assert all(r.status == "execution_failed" for r in result.requirements)
    assert len(client.calls) == len(tool.calls) == 1
    assert result.trace.model_calls == 1


def test_tool_budget_blocks_additional_execution_but_not_previous_resolution():
    worker, _, tool = subject(
        [search(), resolve(0), search("Need another evidence source")],
        config=ResearchRuntimeConfig(max_tool_calls_per_worker=1),
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "partial_success" and len(tool.calls) == 1
    assert [r.status for r in result.requirements] == ["supported", "execution_failed"]
    assert result.error.code == "budget_exhausted"


def test_dependency_retry_exhaustion_is_execution_failure_not_insufficient():
    tool = FakeSearch([TimeoutError(), TimeoutError(), TimeoutError()])
    worker, _, _ = subject([search()], tool=tool)
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed"
    assert result.requirements[0].status == "execution_failed"
    assert len(tool.calls) == result.trace.tool_calls == 3
    assert result.trace.retry_count == 2
    assert result.trace.dependency_calls == 2
    assert result.trace.retried_dependency_calls == 1


def test_malformed_model_protocol_preserves_already_resolved_requirement():
    worker, _, _ = subject(
        [search(), resolve(0), LLMResponse(content="Unstructured answer")]
    )
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "partial_success"
    assert [r.status for r in result.requirements] == ["supported", "execution_failed"]


def test_worker_cancellation_rethrows_and_preserves_resolved_progress():
    async def scenario():
        entered = asyncio.Event()

        class Client:
            def __init__(self):
                self.responses = iter([search(), resolve(0)])

            async def complete(self, *, messages, tools):
                try:
                    return next(self.responses)
                except StopIteration:
                    entered.set()
                    await asyncio.Event().wait()

        worker, _, _ = subject([], client=Client())
        run = asyncio.create_task(worker.run(task(2)))
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        result = worker.last_result
        assert result.status == "cancelled"
        assert [r.status for r in result.requirements] == ["supported", "cancelled"]
        assert result.trace.status == "cancelled"

    asyncio.run(scenario())


def test_separate_workers_do_not_share_observed_evidence():
    async def scenario():
        gate = ResourceGate()
        other = TextEvidence(
            evidence_id="ev-other", paper_id="paper-other", page=1, text="Other paper."
        )
        resolver = Resolver((EV, other))
        first, _, _ = subject([search(), resolve()], gate=gate, resolver=resolver)
        second, client, tool = subject(
            [
                resolve(paper_id="paper-other"),
                resolve(status="insufficient_evidence", paper_id="paper-other"),
            ],
            gate=gate,
            resolver=resolver,
        )
        results = await asyncio.gather(
            first.run(task()), second.run(task(paper_id="paper-other"))
        )
        assert [r.status for r in results] == ["completed", "completed"]
        assert results[0].requirements[0].status == "supported"
        assert results[1].requirements[0].status == "insufficient_evidence"
        assert not tool.calls and last_context(client)["collected_evidence"] == []

    asyncio.run(scenario())


def test_batch_resolution_is_atomic_when_one_citation_is_invalid():
    invalid = action(
        "resolve_requirements",
        resolutions=[
            resolve(0).tool_calls[0].arguments,
            resolve(1, evidence_ids=["ev-fake"]).tool_calls[0].arguments,
        ],
    )
    valid = action(
        "resolve_requirements",
        resolutions=[
            resolve(0).tool_calls[0].arguments,
            resolve(1, status="insufficient_evidence").tool_calls[0].arguments,
        ],
    )
    worker, client, tool = subject([search(), invalid, valid])
    result = asyncio.run(worker.run(task(2)))
    assert result.status == "completed" and len(tool.calls) == 1
    assert [r.status for r in result.requirements] == [
        "supported",
        "insufficient_evidence",
    ]
    context = last_context(client)
    assert context["resolved_requirements"] == []
    assert len(context["unresolved_requirements"]) == 2
    assert "error" in json.dumps(context["recent_observations"])


def test_exact_redundant_search_is_observed_without_another_tool_execution():
    worker, client, tool = subject(
        [search(), search(), resolve()],
        config=ResearchRuntimeConfig(max_tool_calls_per_worker=1),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and len(tool.calls) == 1
    assert result.trace.tool_calls == 1 and result.trace.model_calls == 3
    context = last_context(client)
    assert "redundant_action" in json.dumps(context["recent_observations"])
    assert result.trace.actions[1]["issue_code"] == "redundant_action"
    assert result.trace.actions[1]["status"] == "error"


def test_retries_capped_by_tool_budget_have_budget_exhausted_reason():
    tool = FakeSearch([ConnectionError(), ConnectionError(), ConnectionError()])
    worker, _, _ = subject(
        [search()],
        tool=tool,
        config=ResearchRuntimeConfig(max_tool_calls_per_worker=2),
    )
    result = asyncio.run(worker.run(task()))
    assert len(tool.calls) == result.trace.tool_calls == 2
    assert result.trace.retry_count == 1
    assert result.status == "failed"
    assert result.requirements[0].status == "execution_failed"
    assert result.error.code == "budget_exhausted"


def multimodal_subject(*, failures=0, model_budget=4):
    class FigureInput(BaseModel):
        paper_id: NonEmpty
        figure_id: NonEmpty

    class AnalysisInput(BaseModel):
        question: NonEmpty
        figure_evidence_id: NonEmpty
        context_evidence_ids: list[NonEmpty] = Field(default_factory=list)

    class FindFigure(Tool):
        name = "find_figure"
        description = "Lookup a prepared paper-local figure."
        input_model = FigureInput

        def execute(self, arguments):
            return ToolObservation(
                status="success",
                tool_name=self.name,
                summary="Prepared figure found.",
                payload={
                    "figure": FIGURE.model_dump(exclude={"asset_path"})
                    | {"asset_ref": f"asset:{FIGURE.evidence_id}"}
                },
            )

    class AnalyzeFigure(Tool):
        name = "analyze_figure"
        description = "Analyze an observed paper-local figure."
        input_model = AnalysisInput

        def __init__(self):
            self.calls = 0

        def execute(self, arguments):
            self.calls += 1
            if self.calls <= failures:
                raise TimeoutError
            return ToolObservation(
                status="success",
                tool_name=self.name,
                summary="Prepared visual evidence analyzed.",
                payload={
                    "analysis": {
                        "observation": "Visible architecture.",
                        "interpretation": "Architecture information.",
                        "evidence_used": [FIGURE.evidence_id],
                    }
                },
            )

    worker, client, _ = subject(
        [
            action("find_figure", paper_id=PAPER, figure_id="figure-1"),
            action(
                "analyze_figure",
                question="Explain the architecture.",
                figure_evidence_id=FIGURE.evidence_id,
                context_evidence_ids=[],
            ),
            resolve(evidence_ids=[FIGURE.evidence_id]),
        ],
        resolver=Resolver((EV, FIGURE)),
        config=ResearchRuntimeConfig(max_model_calls_per_worker=model_budget),
    )
    analysis = AnalyzeFigure()
    worker.tools.registry.register(FindFigure())
    worker.tools.registry.register(analysis)
    return worker, client, analysis


@pytest.mark.parametrize("failures,model_budget", [(0, 4), (1, 5)])
def test_worker_visual_analysis_counts_physical_vlm_attempts_and_opaque_assets(
    failures, model_budget
):
    worker, client, analysis = multimodal_subject(
        failures=failures, model_budget=model_budget
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed"
    assert analysis.calls == 1 + failures
    assert result.trace.tool_calls == 2 + failures
    assert (
        result.trace.model_calls == len(client.calls) + analysis.calls == model_budget
    )
    assert result.trace.retry_count == failures
    assert result.requirements[0].evidence_ids == [FIGURE.evidence_id]
    context = last_context(client)
    figure_ref = context["collected_evidence"][0]
    assert figure_ref["asset_ref"] == f"asset:{FIGURE.evidence_id}"
    assert "asset_path" not in figure_ref
    assert "/tmp/private" not in result.model_dump_json()
    assert worker.llm.resource_gate.peaks["vlm"] == 1


def test_remaining_model_budget_caps_vlm_retry_before_another_physical_attempt():
    worker, client, analysis = multimodal_subject(failures=3, model_budget=3)
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed" and result.error.code == "budget_exhausted"
    assert len(client.calls) == 2 and analysis.calls == 1
    assert result.trace.model_calls == 3 and result.trace.tool_calls == 2
    assert result.trace.retry_count == 0
    assert result.requirements[0].status == "execution_failed"


@pytest.mark.parametrize(
    "corruption", ["foreign_paper", "unknown_identity", "altered_text"]
)
def test_corrupted_successful_tool_evidence_is_execution_failure(corruption):
    foreign = TextEvidence(
        evidence_id="ev-foreign",
        paper_id="paper-other",
        page=1,
        text="Other paper evidence.",
    )
    record = EV.model_dump()
    if corruption == "foreign_paper":
        record = foreign.model_dump()
    elif corruption == "unknown_identity":
        record["evidence_id"] = "ev-unknown"
    else:
        record["text"] = "Content was altered outside the prepared corpus."
    bad = ToolObservation(
        status="success",
        tool_name="search_text",
        summary="Claimed successful retrieval with invalid evidence.",
        payload={"evidence": [record]},
    )
    worker, client, _ = subject(
        [search(), resolve(status="insufficient_evidence")],
        tool=FakeSearch([bad]),
        resolver=Resolver((EV, foreign)),
    )
    result = asyncio.run(worker.run(task()))
    assert result.status == "failed"
    assert result.requirements[0].status == "execution_failed"
    assert len(client.calls) == 1
    assert worker.state.collected_evidence == {}


def test_invalid_action_reasoning_extras_are_not_exposed_in_trace():
    invalid = action(
        "search_text",
        query="Find prepared evidence",
        paper_ids=[PAPER],
        top_k=1,
        reasoning="private hidden argument rationale",
        chain_of_thought="private hidden chain contents",
        internal_thought="private hidden thought contents",
    )
    worker, _, tool = subject([invalid, resolve(status="insufficient_evidence")])
    result = asyncio.run(worker.run(task()))
    assert result.status == "completed" and not tool.calls
    assert result.trace.actions[0]["issue_code"] == "invalid_arguments"
    arguments = result.trace.actions[0]["arguments"]
    assert not {"reasoning", "chain_of_thought", "internal_thought"} & arguments.keys()
    serialized = result.model_dump_json()
    for content in [
        "private hidden argument rationale",
        "private hidden chain contents",
        "private hidden thought contents",
    ]:
        assert content not in serialized
