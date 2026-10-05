"""Deterministic find -> prepared page -> analyze -> validate -> answer."""

from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.citations import CitationValidator
from app.evidence.models import FigureEvidence, NonEmpty, Page
from app.evidence.resolver import EvidenceResolver
from app.tools.executor import ToolExecutor
from app.tools.observations import Status, ToolExecutionTrace, ToolObservation


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: NonEmpty
    paper_id: NonEmpty
    page: Page
    figure_id: NonEmpty | None = None


class MultimodalQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Status
    answer: str | None
    citations: list[Citation]
    observations: list[ToolObservation]
    limitations: list[str]
    trace: list[ToolExecutionTrace]
    total_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    tool_calls: int = Field(ge=0)
    citations_validated: bool = False


class MultimodalQAService:
    def __init__(self, executor: ToolExecutor, resolver: EvidenceResolver):
        self.executor, self.resolver = executor, resolver

    def answer(
        self,
        question: str,
        paper_id: str,
        figure_id: str,
        *,
        context_page: int | None = None,
    ) -> MultimodalQAResult:
        # Validate before executing any capability. Context page is caller-selected or figure page,
        # never chosen by a model. Each request slices its own traces on repeated service use.
        from app.tools.figure import AnalyzeFigureInput
        from app.tools.paper import FindFigureInput, ReadPageInput

        FindFigureInput(paper_id=paper_id, figure_id=figure_id)
        AnalyzeFigureInput(
            question=question, figure_evidence_id="pending", context_evidence_ids=[]
        )
        if context_page is not None:
            ReadPageInput(paper_id=paper_id, page=context_page)
        started, trace_start = perf_counter(), len(self.executor.traces)
        observations = []

        def finish(
            status, answer=None, citations=None, limitations=None, validated=False
        ):
            trace = list(self.executor.traces[trace_start:])
            return MultimodalQAResult(
                status=status,
                answer=answer,
                citations=citations or [],
                observations=observations,
                limitations=limitations or [],
                trace=trace,
                tool_calls=len(trace),
                total_latency_ms=(perf_counter() - started) * 1000,
                citations_validated=validated,
            )

        def call(name, arguments):
            observation = self.executor.execute(name, arguments)
            observations.append(observation)
            return observation

        found = call("find_figure", {"paper_id": paper_id, "figure_id": figure_id})
        if found.status != "success":
            return finish(found.status, limitations=[found.summary])
        figure = found.payload["figure"]
        page = call(
            "read_page",
            {
                "paper_id": figure["paper_id"],
                "page": figure["page"] if context_page is None else context_page,
            },
        )
        if page.status != "success":
            return finish(page.status, limitations=[page.summary])
        context_ids = [e["evidence_id"] for e in page.payload["evidence"]]
        analyzed = call(
            "analyze_figure",
            {
                "question": question,
                "figure_evidence_id": figure["evidence_id"],
                "context_evidence_ids": context_ids,
            },
        )
        if analyzed.status != "success":
            return finish(analyzed.status, limitations=[analyzed.summary])
        analysis = analyzed.payload["analysis"]
        used = CitationValidator().validate(
            analysis["evidence_used"], [figure["evidence_id"], *context_ids]
        )
        citations = []
        for evidence_id in used:
            item = self.resolver.resolve(evidence_id)
            citations.append(
                Citation(
                    evidence_id=item.evidence_id,
                    paper_id=item.paper_id,
                    page=item.page,
                    figure_id=item.figure_id
                    if isinstance(item, FigureEvidence)
                    else None,
                )
            )
        return finish(
            "success",
            analysis["interpretation"],
            citations,
            list(analysis["limitations"])
            + [
                "Only prepared page regions were supplied; citation validation checks identity, not semantic correctness."
            ],
            True,
        )
