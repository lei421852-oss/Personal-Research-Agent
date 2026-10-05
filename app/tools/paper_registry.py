"""Register the three domain tools, without connecting an LLM."""

from app.evidence.corpus import PaperCorpus
from app.evidence.repository import PaperEvidenceRepository
from app.retrieval.contracts import TextRetrieval
from app.tools.paper import FindFigureTool, ReadPageTool, SearchTextTool
from app.tools.registry import ToolRegistry


def create_paper_registry(
    corpus: PaperCorpus, retrieval: TextRetrieval
) -> ToolRegistry:
    papers = PaperEvidenceRepository(corpus)
    registry = ToolRegistry()
    registry.register(SearchTextTool(retrieval, papers))
    registry.register(FindFigureTool(papers))
    registry.register(ReadPageTool(papers))
    return registry
