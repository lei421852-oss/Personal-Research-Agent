"""需求覆盖协议；只校验结构，不将模型自报覆盖等同于语义核验。"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.evidence.models import NonEmpty
from app.research.models import MAX_DIMENSIONS, DimensionID, ResearchModel

ASPECTS_BY_DIMENSION: dict[str, tuple[str, ...]] = {
    "vision_encoder": ("type", "configuration", "feature_output"),
    "vision_language_bridge": (
        "connector",
        "visual_transform",
        "language_interface",
    ),
    "frozen_trainable_scope": (
        "training_stages",
        "frozen_modules",
        "updated_modules",
    ),
    "training_objective": ("training_stages", "losses", "optimization_targets"),
    "language_model": ("model_family", "architecture", "update_state"),
}
DEFAULT_ASPECTS = ("requested_facts",)


class CoverageFact(ResearchModel):
    """由策略提出的本论文事实；证据真实性与所属关系由 Worker 另行检查。"""

    subject_paper_id: NonEmpty
    dimension_id: DimensionID
    text: NonEmpty
    evidence_ids: list[NonEmpty] = Field(min_length=1)
    assertion_kind: Literal["descriptive", "exclusive", "limited_absence"] = (
        "descriptive"
    )
    support_quote: NonEmpty | None = None

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, identities):
        """保留顺序地去重，正式引用仍须经过既有 CitationValidator。"""
        return list(dict.fromkeys(identities))

    @model_validator(mode="after")
    def bounded_assertion_quote(self):
        """排他或限定缺失声明须携带原文；是否足够支持仍是语义问题。"""
        if self.assertion_kind != "descriptive" and self.support_quote is None:
            raise ValueError("Bounded assertions require an exact supporting quote")
        return self


class CoverageAspect(ResearchModel):
    """一个属性的覆盖声明；缺失只保留缺口，不夹带未核验事实。"""

    aspect_id: NonEmpty
    status: Literal["covered", "missing", "not_applicable"]
    fact: CoverageFact | None = None

    @model_validator(mode="after")
    def status_fact_binding(self):
        if self.status == "missing" and self.fact is not None:
            raise ValueError("Missing aspects cannot include a fact")
        if self.status != "missing" and self.fact is None:
            raise ValueError("Covered and not-applicable aspects require a fact")
        return self


class CoverageStage(ResearchModel):
    """策略从证据识别训练阶段；不预设论文必须拥有几阶段。"""

    stage_id: NonEmpty
    name: NonEmpty
    aspects: list[CoverageAspect]

    @field_validator("aspects")
    @classmethod
    def unique_aspect_ids(cls, aspects):
        identities = [aspect.aspect_id for aspect in aspects]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate stage aspect_id")
        return aspects


class CoverageResolution(ResearchModel):
    """兼容旧完成状态；最终 Claim 与引用由覆盖事实确定性组装。"""

    requirement_id: NonEmpty
    status: Literal["supported", "insufficient_evidence"]
    coverage: list[CoverageAspect]
    stages: list[CoverageStage] = Field(default_factory=list)

    @field_validator("coverage")
    @classmethod
    def unique_aspect_ids(cls, aspects):
        identities = [aspect.aspect_id for aspect in aspects]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate coverage aspect_id")
        return aspects

    @field_validator("stages")
    @classmethod
    def unique_stage_ids(cls, stages):
        identities = [stage.stage_id for stage in stages]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate coverage stage_id")
        return stages


class BatchResolution(ResearchModel):
    """批量动作只表达候选提交；整体原子性由 Worker 的提交边界保证。"""

    resolutions: list[CoverageResolution] = Field(
        min_length=1, max_length=MAX_DIMENSIONS
    )

    @field_validator("resolutions")
    @classmethod
    def unique_requirements(cls, resolutions):
        identities = [resolution.requirement_id for resolution in resolutions]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate coverage requirement_id")
        return resolutions
