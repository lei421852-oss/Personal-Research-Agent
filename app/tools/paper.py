"""将论文领域能力转换为不含本地路径的 Agent 观察结果。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.evidence.models import FigureEvidence, NonEmpty, Page, TextEvidence
from app.evidence.repository import PaperEvidenceAccess
from app.retrieval.contracts import TextRetrieval
from app.tools.base import Tool
from app.tools.observations import ObservationIssue, ToolObservation


class PaperToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchTextInput(PaperToolInput):
    query: NonEmpty
    paper_ids: list[NonEmpty] | None = Field(
        default=None,
        description=(
            "When the user explicitly names one or more known papers, you MUST supply "
            "their corresponding paper IDs from the available papers catalog. Use null "
            "only for whole-corpus questions, cross-paper discovery, or when no paper "
            "is explicitly specified. A comparison of named papers must include all "
            "named paper IDs. [] searches no papers."
        ),
    )
    top_k: Annotated[StrictInt, Field(gt=0)] = 3


class FindFigureInput(PaperToolInput):
    paper_id: NonEmpty
    figure_id: NonEmpty


class ReadPageInput(PaperToolInput):
    paper_id: NonEmpty
    page: Page


class PublicTextEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: NonEmpty
    paper_id: NonEmpty
    page: Page
    text: NonEmpty


class PublicRetrievedEvidence(PublicTextEvidence):
    retrieval_score: float = Field(allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, allow_inf_nan=False)


class PublicFigureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: NonEmpty
    paper_id: NonEmpty
    page: Page
    figure_id: NonEmpty
    caption: NonEmpty
    asset_ref: NonEmpty


def _public_text(evidence: TextEvidence) -> dict:
    return PublicTextEvidence(**evidence.model_dump(exclude={"modality"})).model_dump(
        mode="json"
    )


def _public_figure(evidence: FigureEvidence) -> dict:
    return PublicFigureEvidence(
        evidence_id=evidence.evidence_id,
        paper_id=evidence.paper_id,
        page=evidence.page,
        figure_id=evidence.figure_id,
        caption=evidence.caption,
        asset_ref=f"asset:{evidence.evidence_id}",
    ).model_dump(mode="json")


def _empty(tool_name: str, summary: str, payload: dict) -> ToolObservation:
    return ToolObservation(
        status="empty",
        tool_name=tool_name,
        summary=summary,
        payload=payload,
        issue=ObservationIssue(code="empty_result", retryable=False),
    )


class SearchTextTool(Tool[SearchTextInput, ToolObservation]):
    name = "search_text"
    description = (
        "Retrieve prepared paper text with pre-search paper filtering. When the user "
        "explicitly names one or more known papers, you MUST restrict paper_ids to "
        "the corresponding IDs in the available papers catalog. This also applies "
        "to comparisons of explicitly named papers: include all named paper IDs. "
        "Use paper_ids=null only for whole-corpus questions, cross-paper discovery, "
        "or when no paper is explicitly specified. [] searches none. Unknown papers "
        "are errors. The model must select scope; the controller does not infer or "
        "rewrite paper IDs from the question."
    )
    input_model = SearchTextInput

    def __init__(self, retrieval: TextRetrieval, papers: PaperEvidenceAccess):
        self.retrieval, self.papers = retrieval, papers

    def execute(self, arguments: SearchTextInput) -> ToolObservation:
        # 在检索前解析调用方给出的范围；None 保持全库，不从用户问题猜论文。
        scopes = (
            None
            if arguments.paper_ids is None
            else list(
                dict.fromkeys(self.papers.resolve_paper(p) for p in arguments.paper_ids)
            )
        )
        results = self.retrieval.search_text(arguments.query, scopes, arguments.top_k)
        if len(results) > arguments.top_k:
            raise ValueError("Retrieval violated the requested Top-K limit")
        evidence = []
        for result in results:
            # 再核对返回结果的范围，防止后端未执行过滤却悄悄返回其他论文。
            if scopes is not None and result.evidence.paper_id not in scopes:
                raise ValueError("Retrieval violated paper scope")
            self.papers.validate_text(result.evidence)
            evidence.append(
                PublicRetrievedEvidence(
                    **_public_text(result.evidence),
                    retrieval_score=result.retrieval_score,
                    rerank_score=result.rerank_score,
                ).model_dump(mode="json")
            )
        payload = {"paper_ids": scopes, "evidence": evidence}
        if not evidence:
            return _empty(
                self.name, "No text evidence matched the requested scope.", payload
            )
        return ToolObservation(
            status="success",
            tool_name=self.name,
            summary=f"Retrieved {len(evidence)} text evidence items.",
            payload=payload,
        )


class FindFigureTool(Tool[FindFigureInput, ToolObservation]):
    name = "find_figure"
    description = "Find an exact prepared figure by paper alias/version ID and figure ID. Returns an opaque asset reference, never a local path."
    input_model = FindFigureInput

    def __init__(self, papers: PaperEvidenceAccess):
        self.papers = papers

    def execute(self, arguments: FindFigureInput) -> ToolObservation:
        canonical = self.papers.resolve_paper(arguments.paper_id)
        figure = self.papers.find_figure(canonical, arguments.figure_id)
        if figure is None:
            return _empty(
                self.name,
                "Requested figure is not in the prepared corpus.",
                {"figure": None},
            )
        if figure.paper_id != canonical or figure.figure_id != arguments.figure_id:
            raise ValueError("Figure lookup violated the requested identity")
        return ToolObservation(
            status="success",
            tool_name=self.name,
            summary="Found the requested prepared figure.",
            payload={"figure": _public_figure(figure)},
        )


class ReadPageTool(Tool[ReadPageInput, ToolObservation]):
    name = "read_page"
    description = "Read only already-prepared regions of a one-based PDF page. An unprepared page is empty; this tool never parses PDF on demand."
    input_model = ReadPageInput

    def __init__(self, papers: PaperEvidenceAccess):
        self.papers = papers

    def execute(self, arguments: ReadPageInput) -> ToolObservation:
        canonical = self.papers.resolve_paper(arguments.paper_id)
        page = self.papers.read_page(canonical, arguments.page)
        if page.paper_id != canonical or page.page != arguments.page:
            raise ValueError("Page lookup violated the requested identity")
        payload = {
            "paper_id": page.paper_id,
            "page": page.page,
            "coverage": "prepared_regions_only",
            "prepared_pages": list(page.prepared_pages),
            "evidence": [_public_text(e) for e in page.evidence],
            "figures": [_public_figure(e) for e in page.figures],
        }
        if not page.prepared:
            return _empty(
                self.name,
                "Requested page has not been prepared; no PDF parsing was attempted.",
                payload | {"reason": "page_not_prepared"},
            )
        if not page.evidence and not page.figures:
            return _empty(self.name, "Prepared page contains no evidence.", payload)
        return ToolObservation(
            status="success",
            tool_name=self.name,
            summary=f"Read {len(page.evidence)} text items and {len(page.figures)} figures from prepared regions; this is not a full-page PDF read.",
            payload=payload,
        )
