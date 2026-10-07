"""独立论文领域模型，不依赖向量数据库、API 或 Agent 工具。"""

import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
)

from app.evidence.models import NonEmpty

MAX_PROFILE_ITEMS = 5
REPRESENTATION_VERSION = "paper-profile-v1"
PROFILE_FIELDS = ("tasks", "methods", "capabilities")
ProfileTerm = Annotated[
    StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
]
ProfileTerms = Annotated[list[ProfileTerm], Field(max_length=MAX_PROFILE_ITEMS)]


def deduplicate_terms(value):
    """只去掉大小写和空白差异的重复项，不猜测语义或添加同义词。"""
    if not isinstance(value, list):
        return value
    result, seen = [], set()
    for term in value:
        if not isinstance(term, str):
            result.append(term)
            continue
        term = re.sub(r"\s+", " ", term).strip()
        key = term.casefold()
        if key not in seen:
            result.append(term)
            seen.add(key)
    return result


class PaperProfile(BaseModel):
    """模型必须提交全部三个字段；缺乏来源依据时提交空列表。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tasks: ProfileTerms
    methods: ProfileTerms
    capabilities: ProfileTerms

    @field_validator(*PROFILE_FIELDS, mode="before")
    @classmethod
    def unique_terms(cls, value):
        return deduplicate_terms(value)

    @classmethod
    def empty(cls):
        return cls(tasks=[], methods=[], capabilities=[])

    @property
    def has_terms(self):
        return any(getattr(self, field) for field in PROFILE_FIELDS)


class PaperDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    paper_id: NonEmpty
    title: NonEmpty
    abstract: NonEmpty
    authors: list[NonEmpty] = Field(min_length=1)
    year: StrictInt = Field(ge=1900, le=2100)
    venue: NonEmpty
    keywords: list[NonEmpty] = Field(default_factory=list)
    tasks: ProfileTerms = Field(default_factory=list)
    methods: ProfileTerms = Field(default_factory=list)
    capabilities: ProfileTerms = Field(default_factory=list)

    @field_validator(*PROFILE_FIELDS, mode="before")
    @classmethod
    def unique_terms(cls, value):
        return deduplicate_terms(value)

    @property
    def profile(self) -> PaperProfile:
        return PaperProfile(**{field: getattr(self, field) for field in PROFILE_FIELDS})

    @property
    def retrieval_text(self) -> str:
        """集中管理离线索引文本；检索器和存储器只消费这一表示。"""
        return "\n".join(
            [
                f"Title: {self.title}",
                f"Abstract: {self.abstract}",
                f"Tasks: {'; '.join(self.tasks)}",
                f"Methods: {'; '.join(self.methods)}",
                f"Capabilities: {'; '.join(self.capabilities)}",
                f"Keywords: {'; '.join(self.keywords)}",
            ]
        )

    @property
    def legacy_retrieval_text(self) -> str:
        """仅用于只读打开旧索引；新建索引一律使用带字段名的表示。"""
        return "\n".join([self.title, self.abstract, " ".join(self.keywords)]).strip()


class RetrievedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paper: PaperDocument
    score: float = Field(allow_inf_nan=False)
    rank: StrictInt = Field(gt=0)
