"""Prepared-corpus access only; no on-demand PDF parsing."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.evidence.corpus import PaperCorpus
from app.evidence.errors import EvidenceDependencyError, UnknownPaperError
from app.evidence.models import FigureEvidence, TextEvidence


@dataclass(frozen=True)
class PreparedPage:
    paper_id: str
    page: int
    prepared_pages: tuple[int, ...]
    evidence: tuple[TextEvidence, ...]
    figures: tuple[FigureEvidence, ...]

    @property
    def prepared(self) -> bool:
        return self.page in self.prepared_pages


class PaperEvidenceAccess(Protocol):
    def resolve_paper(self, paper_id: str) -> str: ...
    def find_figure(self, paper_id: str, figure_id: str) -> FigureEvidence | None: ...
    def read_page(self, paper_id: str, page: int) -> PreparedPage: ...
    def validate_text(self, evidence: TextEvidence) -> None: ...


class PaperEvidenceRepository:
    def __init__(self, corpus: PaperCorpus):
        self.corpus = corpus
        self._texts = {e.evidence_id: e for e in corpus.text_evidence}
        self._figures = {(e.paper_id, e.figure_id): e for e in corpus.figure_evidence}
        self._prepared = {
            m.paper_id: tuple(p.page for p in m.pages) for m in corpus.metadata
        }

    def resolve_paper(self, paper_id: str) -> str:
        try:
            return self.corpus.paper_aliases[paper_id]
        except KeyError as exc:
            raise UnknownPaperError("Paper is not prepared") from exc

    def validate_text(self, evidence: TextEvidence) -> None:
        if self._texts.get(evidence.evidence_id) != evidence:
            raise ValueError("Retrieval returned evidence outside this corpus snapshot")

    def find_figure(self, paper_id: str, figure_id: str) -> FigureEvidence | None:
        canonical = self.resolve_paper(paper_id)
        figure = self._figures.get((canonical, figure_id))
        if figure is not None and not Path(figure.asset_path).is_file():
            raise EvidenceDependencyError("Prepared figure asset is unavailable")
        return figure

    def read_page(self, paper_id: str, page: int) -> PreparedPage:
        canonical = self.resolve_paper(paper_id)
        prepared = self._prepared[canonical]
        if page not in prepared:
            return PreparedPage(canonical, page, prepared, (), ())
        evidence = tuple(
            e
            for e in self.corpus.text_evidence
            if e.paper_id == canonical and e.page == page
        )
        figures = tuple(
            self.find_figure(canonical, e.figure_id)
            for e in self.corpus.figure_evidence
            if e.paper_id == canonical and e.page == page
        )
        return PreparedPage(canonical, page, prepared, evidence, figures)


class FigureAssetResolver:
    """Private backend mapping. Asset references are never interpreted as paths."""

    def __init__(self, corpus: PaperCorpus):
        self._assets = {
            f"asset:{e.evidence_id}": Path(e.asset_path) for e in corpus.figure_evidence
        }

    def resolve(self, asset_ref: str) -> Path:
        if asset_ref not in self._assets:
            raise KeyError("Unknown asset reference")
        path = self._assets[asset_ref]
        if not path.is_file():
            raise EvidenceDependencyError("Prepared figure asset is unavailable")
        return path
