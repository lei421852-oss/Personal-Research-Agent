"""对目录已知但未准备正文的论文，显式报告证据不可用。"""

from app.evidence.errors import UnknownPaperError
from app.evidence.repository import PaperEvidenceRepository


class PaperEvidenceUnavailable(ValueError):
    def __init__(self, paper_id: str):
        self.paper_id = paper_id
        super().__init__("Discovered paper has no prepared evidence")


class DiscoveryEvidenceRepository(PaperEvidenceRepository):
    def __init__(self, corpus, catalog):
        super().__init__(corpus)
        self.discovery_catalog = catalog

    def resolve_paper(self, paper_id: str) -> str:
        try:
            return super().resolve_paper(paper_id)
        except UnknownPaperError:
            if paper_id in self.discovery_catalog.documents:
                raise PaperEvidenceUnavailable(paper_id) from None
            raise
