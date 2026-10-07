"""加载可追溯论文元数据 fixture，与准备好的 PDF Corpus 分开。"""

import json
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.evidence.models import NonEmpty
from app.papers.models import PaperDocument

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2] / "data/paper_discovery/papers.json"
)


class PaperFixtureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: NonEmpty
    source_url: HttpUrl
    source_abstract_sha256: NonEmpty
    abstract_kind: str = Field(pattern="^source_based_summary$")
    document: PaperDocument


class PaperCatalog:
    def __init__(self, records: list[PaperFixtureRecord]):
        ids = [r.document.paper_id for r in records]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate paper_id in discovery fixture")
        self.records = tuple(records)
        self.documents = MappingProxyType(
            {r.document.paper_id: r.document for r in records}
        )

    def resolve(self, paper_id: str) -> PaperDocument:
        return self.documents[paper_id]


def load_paper_catalog(path: str | Path = DEFAULT_FIXTURE) -> PaperCatalog:
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported paper fixture version")
    records = [PaperFixtureRecord.model_validate(r) for r in data["papers"]]
    if not records:
        raise ValueError("Discovery catalog is empty")
    return PaperCatalog(records)
