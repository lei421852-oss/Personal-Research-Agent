"""不可变的论文证据，与具体查询的排序结果分开。"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

NonEmpty = Annotated[StrictStr, Field(min_length=1, pattern=r"\S")]
Page = Annotated[StrictInt, Field(gt=0)]


def make_evidence_id(
    paper_id: str, page: int, modality: str, local_identifier: str
) -> str:
    """固定论文版本、页码、模态和局部标识时生成稳定的证据 ID。"""
    if not paper_id.strip() or not local_identifier.strip():
        raise ValueError("paper_id and local_identifier must be nonempty")
    if type(page) is not int or page <= 0 or modality not in ("text", "figure"):
        raise ValueError("Invalid page or modality")
    identity = json.dumps(
        [paper_id, page, modality, local_identifier], ensure_ascii=False
    )
    return "ev_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: NonEmpty
    paper_id: NonEmpty
    modality: Literal["text", "figure"]
    page: Page  # 从 1 开始的 PDF 页序号，不是论文印刷页码。
    text: NonEmpty


class TextEvidence(Evidence):
    modality: Literal["text"] = "text"


class FigureEvidence(Evidence):
    modality: Literal["figure"] = "figure"
    figure_id: NonEmpty
    caption: NonEmpty
    asset_path: NonEmpty


EvidenceRecord = Annotated[
    TextEvidence | FigureEvidence, Field(discriminator="modality")
]


class RetrievedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence: EvidenceRecord
    retrieval_score: float = Field(allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, allow_inf_nan=False)
