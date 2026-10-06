"""Explicit final action grounded in evidence observed during this run."""

from pydantic import BaseModel, ConfigDict

from app.evidence.models import FigureEvidence, NonEmpty
from app.evidence.resolver import EvidenceResolver
from app.qa.service import Citation


class FinishInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: NonEmpty
    evidence_ids: list[NonEmpty]
    limitations: list[NonEmpty]


class FinishValidationError(ValueError):
    pass


class FinishAction:
    name = "finish"

    @staticmethod
    def schema():
        return {
            "name": "finish",
            "description": "Terminate with an answer supported only by observed evidence. If insufficient, explicitly state limitations; do not invent citations.",
            "input_schema": FinishInput.model_json_schema(),
        }

    def __init__(self, resolver: EvidenceResolver):
        self.resolver = resolver

    def finalize(self, arguments: dict, observed_ids: set[str]):
        args = FinishInput.model_validate(arguments)
        if not set(args.evidence_ids).issubset(observed_ids):
            raise FinishValidationError("Final citations include unobserved evidence")
        if not args.evidence_ids and not args.limitations:
            raise FinishValidationError(
                "An uncited finish must state evidence limitations"
            )
        citations = []
        for identity in dict.fromkeys(args.evidence_ids):
            item = self.resolver.resolve(identity)
            citations.append(
                Citation(
                    evidence_id=identity,
                    paper_id=item.paper_id,
                    page=item.page,
                    figure_id=item.figure_id
                    if isinstance(item, FigureEvidence)
                    else None,
                )
            )
        return args, citations
