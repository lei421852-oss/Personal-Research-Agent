"""只解析当前已校验来源的语料快照中的证据 ID。"""

from app.evidence.corpus import PaperCorpus
from app.evidence.models import FigureEvidence, TextEvidence


class EvidenceResolutionError(ValueError):
    """证据不存在、模态错误或范围不符；原参数不变的重试无法修复。"""


class EvidenceResolver:
    def __init__(self, corpus: PaperCorpus, *, paper_catalog=None):
        self._paper_catalog = tuple(
            (metadata.paper_key.upper(), metadata.paper_id)
            for metadata in corpus.metadata
        )
        self._discovery_catalog = paper_catalog
        self._prepared_ids = frozenset(m.paper_id for m in corpus.metadata)
        self._items = {
            e.evidence_id: e for e in (*corpus.text_evidence, *corpus.figure_evidence)
        }

    @property
    def paper_catalog(self) -> list[dict[str, str]]:
        """返回目录副本；只公开论文名和版本 ID，不暴露本地路径。"""
        if self._discovery_catalog is not None:
            return [
                {
                    "name": r.name,
                    "paper_id": r.document.paper_id,
                    "paper_discovered": True,
                    "paper_evidence_available": r.document.paper_id
                    in self._prepared_ids,
                }
                for r in self._discovery_catalog.records
            ]
        return [
            {"name": name, "paper_id": paper_id}
            for name, paper_id in self._paper_catalog
        ]

    @property
    def paper_ids(self) -> list[str]:
        ids = {item.paper_id for item in self._items.values()}
        if self._discovery_catalog is not None:
            ids.update(self._discovery_catalog.documents)
        return sorted(ids)

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
