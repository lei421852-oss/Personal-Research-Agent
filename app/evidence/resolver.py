"""Resolve only identities in the current, provenance-checked corpus snapshot."""

from app.evidence.corpus import PaperCorpus
from app.evidence.models import FigureEvidence, TextEvidence


class EvidenceResolutionError(ValueError):
    """Unknown evidence or a modality/scope mismatch; unchanged retries cannot help."""


class EvidenceResolver:
    def __init__(self, corpus: PaperCorpus):
        self._items = {
            e.evidence_id: e for e in (*corpus.text_evidence, *corpus.figure_evidence)
        }

    def resolve(self, evidence_id: str) -> TextEvidence | FigureEvidence:
        try:
            return self._items[evidence_id]
        except KeyError:
            raise EvidenceResolutionError(
                "Evidence is outside the current corpus"
            ) from None

    def figure(self, evidence_id: str) -> FigureEvidence:
        item = self.resolve(evidence_id)
        if not isinstance(item, FigureEvidence):
            raise EvidenceResolutionError("Expected figure evidence")
        return item

    def contexts(self, ids: list[str], paper_id: str) -> list[TextEvidence]:
        items = [self.resolve(i) for i in dict.fromkeys(ids)]
        if any(
            not isinstance(e, TextEvidence) or e.paper_id != paper_id for e in items
        ):
            raise EvidenceResolutionError(
                "Context must be text evidence from the figure's paper"
            )
        return items
