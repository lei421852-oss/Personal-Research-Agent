"""论文发现与正文范围访问边界：目录候选不是可引用 Evidence。"""

from pydantic import Field, StrictInt

from app.evidence.errors import UnknownPaperError
from app.evidence.models import NonEmpty
from app.papers.catalog import PaperCatalog
from app.papers.evidence_access import PaperEvidenceUnavailable
from app.tools.base import Tool
from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.paper import PaperToolInput, SearchTextInput, SearchTextTool

ABSTRACT_SNIPPET_CHARS = 280


class SearchPapersInput(PaperToolInput):
    query: NonEmpty
    top_k: StrictInt = Field(default=20, gt=0)


class SearchPapersTool(Tool[SearchPapersInput, ToolObservation]):
    name = "search_papers"
    description = (
        "Discover candidate papers for open research questions when target papers are unknown. "
        "Do not call this for explicitly named known papers: use their IDs directly. "
        "Returned metadata is not text/figure evidence and cannot be cited by finish. "
        "Use candidate paper IDs as search_text scope; check paper_evidence_available."
    )
    input_model = SearchPapersInput

    def __init__(self, retriever, catalog: PaperCatalog, prepared_paper_ids: list[str]):
        self.retriever, self.catalog = retriever, catalog
        self.prepared = frozenset(prepared_paper_ids)

    def execute(self, arguments: SearchPapersInput) -> ToolObservation:
        hits = self.retriever.search(arguments.query, arguments.top_k)
        candidates = []
        for hit in hits:
            if self.catalog.resolve(hit.paper.paper_id) != hit.paper:
                raise ValueError("Paper index result differs from current catalog")
            candidates.append(
                {
                    "paper_id": hit.paper.paper_id,
                    "title": hit.paper.title,
                    "year": hit.paper.year,
                    "venue": hit.paper.venue,
                    "score": hit.score,
                    "abstract_snippet": hit.paper.abstract[:ABSTRACT_SNIPPET_CHARS],
                    "paper_discovered": True,
                    "paper_evidence_available": hit.paper.paper_id in self.prepared,
                }
            )
        return ToolObservation(
            status="success" if candidates else "empty",
            tool_name=self.name,
            summary=f"Discovered {len(candidates)} candidate papers; metadata is not citable evidence.",
            payload={
                "query": arguments.query,
                "top_k": arguments.top_k,
                "papers": candidates,
            },
            issue=None
            if candidates
            else ObservationIssue(code="empty_result", retryable=False),
        )


class DiscoveryScopedTextTool(SearchTextTool):
    """沿用正文工具；对已发现但未准备的论文返回结构化不可用结果。"""

    def __init__(self, retrieval, papers, catalog: PaperCatalog):
        super().__init__(retrieval, papers)
        self.catalog = catalog

    def execute(self, arguments: SearchTextInput) -> ToolObservation:
        if not arguments.paper_ids:
            return super().execute(arguments)
        available, unavailable = [], []
        for identity in arguments.paper_ids:
            try:
                canonical = self.papers.resolve_paper(identity)
            except (UnknownPaperError, PaperEvidenceUnavailable):
                if identity not in self.catalog.documents:
                    raise
                unavailable.append(identity)
            else:
                available.append(canonical)
        if not unavailable:
            return super().execute(arguments)
        availability = {
            "paper_discovered": True,
            "paper_evidence_available": bool(available),
            "requested_paper_ids": list(dict.fromkeys(arguments.paper_ids)),
            "unavailable_paper_ids": list(dict.fromkeys(unavailable)),
            "availability_status": "partial" if available else "unavailable",
        }
        if not available:
            return ToolObservation(
                status="empty",
                tool_name=self.name,
                summary="Requested papers are known to discovery, but no prepared text evidence is available.",
                payload={
                    **availability,
                    "paper_ids": arguments.paper_ids,
                    "evidence": [],
                },
                issue=ObservationIssue(code="empty_result", retryable=False),
            )
        # 只访问调用方请求的可用子集；明确列出缺失部分，不扩大范围补结果。
        observation = super().execute(
            arguments.model_copy(update={"paper_ids": list(dict.fromkeys(available))})
        )
        return observation.model_copy(
            update={"payload": observation.payload | availability}
        )
