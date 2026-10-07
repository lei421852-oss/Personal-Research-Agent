"""注册论文工具，并按需加入基于证据的 VLM 分析工具。"""

from app.evidence.corpus import PaperCorpus
from app.evidence.repository import PaperEvidenceRepository
from app.retrieval.contracts import TextRetrieval
from app.tools.paper import FindFigureTool, ReadPageTool, SearchTextTool
from app.tools.registry import ToolRegistry
from app.vision.analysis import FigureAnalyzer


def create_paper_registry(
    corpus: PaperCorpus,
    retrieval: TextRetrieval,
    *,
    analyzer: FigureAnalyzer | None = None,
    paper_retriever=None,
    paper_catalog=None,
) -> ToolRegistry:
    if paper_catalog is None:
        papers = PaperEvidenceRepository(corpus)
    else:
        from app.papers.evidence_access import DiscoveryEvidenceRepository

        papers = DiscoveryEvidenceRepository(corpus, paper_catalog)
    registry = ToolRegistry()
    if (paper_retriever is None) != (paper_catalog is None):
        raise ValueError("Discovery retriever and catalog must be supplied together")
    if paper_catalog is None:
        registry.register(SearchTextTool(retrieval, papers))
    else:
        from app.tools.discovery import DiscoveryScopedTextTool, SearchPapersTool

        registry.register(DiscoveryScopedTextTool(retrieval, papers, paper_catalog))
        registry.register(
            SearchPapersTool(
                paper_retriever, paper_catalog, [m.paper_id for m in corpus.metadata]
            )
        )
    registry.register(FindFigureTool(papers))
    registry.register(ReadPageTool(papers))
    if analyzer is not None:
        from app.evidence.resolver import EvidenceResolver
        from app.tools.figure import AnalyzeFigureTool

        registry.register(AnalyzeFigureTool(EvidenceResolver(corpus), analyzer))
    return registry
