"""综合报告的受限协议：模型选择结构，程序保管主张正文与引用。"""

from typing import Literal

from pydantic import Field, model_validator

from app.evidence.models import NonEmpty
from app.research.models import ResearchModel, ResearchPlan
from app.research.review_models import NonnegativeInt, ReviewEvidence

MissingKind = Literal[
    "insufficient_evidence",
    "review_rejected",
    "review_unavailable",
    "execution_failed",
    "cancelled",
]
CellStatus = Literal[
    "verified",
    "insufficient_evidence",
    "review_rejected",
    "review_unavailable",
    "execution_failed",
    "cancelled",
]
ReportTitle = Literal[
    "Evidence-grounded research report", "Verified findings and evidence gaps"
]
SectionTitle = Literal["Verified findings", "Evidence coverage", "Side-by-side claims"]


class SynthesisIssue(ResearchModel):
    code: NonEmpty
    message: NonEmpty
    requirement_id: NonEmpty | None = None


class VerifiedClaim(ResearchModel):
    claim_id: NonEmpty
    requirement_id: NonEmpty
    paper_id: NonEmpty
    dimension_id: NonEmpty
    text: NonEmpty
    evidence: list[ReviewEvidence] = Field(min_length=1)

    @property
    def evidence_ids(self):
        return [e.evidence_id for e in self.evidence]

    @model_validator(mode="after")
    def provenance(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate claim evidence")
        if any(e.paper_id != self.paper_id for e in self.evidence):
            raise ValueError("Claim evidence belongs to another paper")
        return self


class MissingEvidenceStatus(ResearchModel):
    requirement_id: NonEmpty
    paper_id: NonEmpty
    dimension_id: NonEmpty
    status: MissingKind
    issue: SynthesisIssue | None = None


class SynthesisCell(ResearchModel):
    requirement_id: NonEmpty
    paper_id: NonEmpty
    dimension_id: NonEmpty
    status: CellStatus
    claim_id: NonEmpty | None = None


class SynthesisInput(ResearchModel):
    plan: ResearchPlan
    cells: list[SynthesisCell]
    verified_claims: list[VerifiedClaim]
    missing_evidence: list[MissingEvidenceStatus]
    allowed_claim_ids: list[NonEmpty]
    allowed_evidence_ids: list[NonEmpty]
    resolved_requirements: NonnegativeInt
    reviewed_requirements: NonnegativeInt

    @model_validator(mode="after")
    def matrix(self):
        from app.research.requirements import expand_requirements

        expected = {r.requirement_id: r for r in expand_requirements(self.plan)}
        cells = {c.requirement_id: c for c in self.cells}
        claims = {c.claim_id: c for c in self.verified_claims}
        missing = {m.requirement_id: m for m in self.missing_evidence}
        if len(cells) != len(self.cells) or set(cells) != set(expected):
            raise ValueError("Input must contain exactly the planned matrix")
        if len(claims) != len(self.verified_claims) or len(missing) != len(
            self.missing_evidence
        ):
            raise ValueError("Duplicate input identity")
        for identity, cell in cells.items():
            original = expected[identity]
            if (cell.paper_id, cell.dimension_id) != (
                original.paper_id,
                original.dimension_id,
            ):
                raise ValueError("Input cell is outside the plan")
            if cell.status == "verified":
                claim = claims.get(cell.claim_id)
                if claim is None or (
                    claim.requirement_id,
                    claim.paper_id,
                    claim.dimension_id,
                ) != (identity, cell.paper_id, cell.dimension_id):
                    raise ValueError("Verified cell needs its exact claim")
                if identity in missing:
                    raise ValueError("Verified cell cannot be missing")
            else:
                item = missing.get(identity)
                if (
                    cell.claim_id is not None
                    or item is None
                    or (item.paper_id, item.dimension_id, item.status)
                    != (cell.paper_id, cell.dimension_id, cell.status)
                ):
                    raise ValueError("Missing cell cannot contain a claim")
        if {c.claim_id for c in self.cells if c.status == "verified"} != set(claims):
            raise ValueError("Unreferenced input claim")
        if set(missing) != {
            c.requirement_id for c in self.cells if c.status != "verified"
        }:
            raise ValueError("Missing statuses do not match matrix")
        evidence_ids = {e for c in self.verified_claims for e in c.evidence_ids}
        if len(set(self.allowed_claim_ids)) != len(self.allowed_claim_ids) or set(
            self.allowed_claim_ids
        ) != set(claims):
            raise ValueError("Allowed claims differ from verified claims")
        if (
            len(set(self.allowed_evidence_ids)) != len(self.allowed_evidence_ids)
            or set(self.allowed_evidence_ids) != evidence_ids
        ):
            raise ValueError("Allowed evidence differs from verified evidence")
        total = len(self.cells)
        if (
            not len(claims)
            <= self.reviewed_requirements
            <= self.resolved_requirements
            <= total
        ):
            raise ValueError("Inconsistent research counters")
        return self


class CellSelection(SynthesisCell):
    evidence_ids: list[NonEmpty] = Field(default_factory=list)


class ComparisonSelection(ResearchModel):
    # 第一版仅组织同维度的逐篇证据，不引入未经语义核验的胜负/等价判断。
    relation: Literal["side_by_side"] = "side_by_side"
    paper_ids: list[NonEmpty] = Field(min_length=2)
    claim_ids: list[NonEmpty] = Field(min_length=2)
    evidence_ids: list[NonEmpty] = Field(min_length=1)


class SectionSelection(ResearchModel):
    dimension_id: NonEmpty
    title: SectionTitle = "Verified findings"
    cells: list[CellSelection] = Field(min_length=1)
    comparisons: list[ComparisonSelection] = Field(default_factory=list)


class SynthesisProposal(ResearchModel):
    title: ReportTitle = "Evidence-grounded research report"
    sections: list[SectionSelection] = Field(min_length=1)


class ReportSection(ResearchModel):
    dimension_id: NonEmpty
    title: NonEmpty
    cells: list[SynthesisCell]
    comparisons: list[ComparisonSelection] = Field(default_factory=list)


class SynthesisMetrics(ResearchModel):
    total_requirements: NonnegativeInt
    verified_requirements: NonnegativeInt
    missing_requirements: NonnegativeInt
    rejected_requirements: NonnegativeInt
    resolved_requirements: NonnegativeInt
    reviewed_requirements: NonnegativeInt
    citation_validation_failures: NonnegativeInt = 0
    synthesis_model_calls: NonnegativeInt = 0
    protocol_corrections: NonnegativeInt = 0
    provider_retries: NonnegativeInt = 0
    synthesis_latency_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    resolution_rate: float = Field(ge=0, le=1)
    evidence_coverage: float = Field(ge=0, le=1)
    review_pass_rate: float | None = Field(default=None, ge=0, le=1)
    report_generation_attempts: NonnegativeInt = 0
    report_generation_successes: NonnegativeInt = 0
    report_generation_success_rate: float | None = Field(default=None, ge=0, le=1)
    report_status: Literal[
        "completed", "generation_degraded", "insufficient_evidence", "cancelled"
    ]
    degraded_generation: bool

    @model_validator(mode="after")
    def count_consistency(self):
        if (
            self.total_requirements
            != self.verified_requirements + self.missing_requirements
        ):
            raise ValueError("Evidence coverage counts do not cover the plan")
        if (
            not self.verified_requirements
            <= self.reviewed_requirements
            <= self.resolved_requirements
            <= self.total_requirements
        ):
            raise ValueError("Research metric denominators are inconsistent")
        if (
            self.reviewed_requirements
            != self.verified_requirements + self.rejected_requirements
        ):
            raise ValueError(
                "Review denominator must include valid pass and rejection outcomes"
            )
        if self.provider_retries > self.synthesis_model_calls:
            raise ValueError("Provider retries exceed physical model calls")
        if self.report_generation_attempts not in (
            0,
            1,
        ) or self.report_generation_successes != int(self.report_status == "completed"):
            raise ValueError("Generation metrics describe one report task")
        if self.report_generation_successes > self.report_generation_attempts:
            raise ValueError("Successful generation requires an attempt")
        if self.degraded_generation != (self.report_status == "generation_degraded"):
            raise ValueError("Degradation flag differs from report status")
        return self


class ResearchReport(ResearchModel):
    title: NonEmpty
    question: NonEmpty
    paper_ids: list[NonEmpty]
    sections: list[ReportSection]
    verified_claims: list[VerifiedClaim]
    missing_evidence: list[MissingEvidenceStatus]
    summary: NonEmpty
    limitations: list[NonEmpty]
    status: Literal[
        "completed", "generation_degraded", "insufficient_evidence", "cancelled"
    ]
    generation_degraded: bool
    issue: SynthesisIssue | None = None
    metrics: SynthesisMetrics
    trace: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def report_consistency(self):
        if (
            self.status != self.metrics.report_status
            or self.generation_degraded != self.metrics.degraded_generation
        ):
            raise ValueError("Report and metric statuses differ")
        if (
            len(self.verified_claims) != self.metrics.verified_requirements
            or len(self.missing_evidence) != self.metrics.missing_requirements
        ):
            raise ValueError("Report facts differ from evidence counts")
        if sum(len(s.cells) for s in self.sections) != self.metrics.total_requirements:
            raise ValueError("Report cells do not cover every requirement")
        if self.status in ("generation_degraded", "cancelled") and self.issue is None:
            raise ValueError("Unsuccessful generation needs a typed issue")
        return self


class SynthesisOutcome(ResearchModel):
    proposal: SynthesisProposal | None = None
    status: Literal[
        "completed", "generation_degraded", "insufficient_evidence", "cancelled"
    ]
    issue: SynthesisIssue | None = None
    model_calls: NonnegativeInt = 0
    provider_retries: NonnegativeInt = 0
    protocol_corrections: NonnegativeInt = 0
    citation_validation_failures: NonnegativeInt = 0
    latency_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    trace: list[dict] = Field(default_factory=list)
