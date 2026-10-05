"""Figure analysis accepts identities, never caller-selected filesystem paths."""

from pydantic import ValidationError

from app.evidence.citations import CitationValidationError, CitationValidator
from app.evidence.models import NonEmpty
from app.evidence.resolver import EvidenceResolver
from app.tools.base import Tool
from app.tools.observations import ToolObservation
from app.tools.paper import PaperToolInput, _public_figure, _public_text
from app.vision.analysis import FigureAnalyzer


class AnalyzeFigureInput(PaperToolInput):
    question: NonEmpty
    figure_evidence_id: NonEmpty
    context_evidence_ids: list[NonEmpty]


class AnalyzeFigureTool(Tool[AnalyzeFigureInput, ToolObservation]):
    name = "analyze_figure"
    description = "Analyze a registered figure with supplied same-paper text evidence; validate citation identities. No arbitrary paths."
    input_model = AnalyzeFigureInput

    def __init__(self, resolver: EvidenceResolver, analyzer: FigureAnalyzer):
        self.resolver, self.analyzer = resolver, analyzer

    def execute(self, arguments: AnalyzeFigureInput) -> ToolObservation:
        figure = self.resolver.figure(arguments.figure_evidence_id)
        contexts = self.resolver.contexts(
            arguments.context_evidence_ids, figure.paper_id
        )
        try:
            analysis = self.analyzer.analyze_figure(
                figure, arguments.question, context_evidence=contexts
            )
        except ValidationError as exc:
            if any(e["loc"] == ("evidence_used",) for e in exc.errors()):
                raise CitationValidationError("Empty or invalid citation list") from exc
            raise
        CitationValidator().validate(
            analysis.evidence_used,
            [figure.evidence_id, *[e.evidence_id for e in contexts]],
        )
        return ToolObservation(
            status="success",
            tool_name=self.name,
            summary="Analyzed figure and supplied context; citation identities validated.",
            payload={
                "figure": _public_figure(figure),
                "evidence": [_public_text(e) for e in contexts],
                "analysis": analysis.model_dump(mode="json"),
                "citations_validated": True,
            },
        )
