"""独立论文领域模型，不依赖向量数据库、API 或 Agent 工具。"""

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.evidence.models import NonEmpty


class PaperDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    paper_id: NonEmpty
    title: NonEmpty
    abstract: NonEmpty
    authors: list[NonEmpty] = Field(min_length=1)
    year: StrictInt = Field(ge=1900, le=2100)
    venue: NonEmpty
    keywords: list[NonEmpty] = Field(default_factory=list)

    @property
    def retrieval_text(self) -> str:
        """固定检索文本，不混入查询、标签或正文 Evidence。"""
        return "\n".join([self.title, self.abstract, " ".join(self.keywords)]).strip()


class RetrievedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paper: PaperDocument
    score: float = Field(allow_inf_nan=False)
    rank: StrictInt = Field(gt=0)
