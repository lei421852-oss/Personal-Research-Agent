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
) -> ToolRegistry:
    papers = PaperEvidenceRepository(corpus)
    registry = ToolRegistry()
    registry.register(SearchTextTool(retrieval, papers))
    registry.register(FindFigureTool(papers))
    registry.register(ReadPageTool(papers))
    if analyzer is not None:
        from app.evidence.resolver import EvidenceResolver
        from app.tools.figure import AnalyzeFigureTool

        registry.register(AnalyzeFigureTool(EvidenceResolver(corpus), analyzer))
    return registry
