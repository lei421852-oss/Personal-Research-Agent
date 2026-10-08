"""候选、人工 Gold 与预测评分的独立契约。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from app.evidence.models import NonEmpty, Page
from app.research.corpus_validation import SHA256, SourceSpan, canonical_hash

TaskType = Literal["claim", "reviewer", "comparison", "abstention"]
Split = Literal["unassigned", "dev", "test"]
Count = Annotated[StrictInt, Field(ge=0)]
RubricScore = Annotated[StrictInt, Field(ge=0, le=2)]


class QualityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def unique(values):
    if len(values) != len(set(values)):
        raise ValueError("Duplicate identity")


class SourceLocation(QualityModel):
    evidence_id: NonEmpty
    paper_id: NonEmpty
    pdf_page: Page
    printed_page: str | None = None
    section_title: NonEmpty
    source_url: NonEmpty
    source_version: NonEmpty
    source_document_sha256: SHA256
    source_span: list[SourceSpan] = Field(min_length=1)
    evidence_field: Literal["text", "caption"]
    quote: NonEmpty


class HumanGoldReview(QualityModel):
    reviewer: NonEmpty
    reviewed_at: datetime
    method: Literal["human_original_pdf_review"]
    verdict: Literal["approved", "rejected", "needs_changes"]
    checked_evidence_ids: list[NonEmpty]
    checked_boundary_scope: StrictBool = False
    notes: NonEmpty

    @model_validator(mode="after")
    def checked(self):
        if self.reviewed_at.tzinfo is None:
            raise ValueError("Human review timestamp requires a timezone")
        unique(self.checked_evidence_ids)
        return self


class AnnotationProvenance(QualityModel):
    reference_origin: Literal["original_prepared_pdf"]
    author: NonEmpty
    created_at: datetime
    method: Literal["coding_agent_source_curation", "human_source_curation"]
    source_notes: NonEmpty
    human_reviews: list[HumanGoldReview] = Field(default_factory=list)

    @model_validator(mode="after")
    def dated(self):
        if self.created_at.tzinfo is None:
            raise ValueError("Annotation creation timestamp requires a timezone")
        return self


class ReviewerTarget(QualityModel):
    claim: NonEmpty
    cited_evidence_ids: list[NonEmpty] = Field(min_length=1)
    expected_label: Literal["passed", "rejected"]
    rationale: NonEmpty
    example_origin: Literal["source_paraphrase", "controlled_contradiction"]


class PaperFact(QualityModel):
    paper_id: NonEmpty
    fact: NonEmpty
    evidence_ids: list[NonEmpty] = Field(min_length=1)
    fact_group_ids: list[NonEmpty] = Field(min_length=1)


class ExpectedDifference(QualityModel):
    paper_ids: list[NonEmpty] = Field(min_length=2)
    statement: NonEmpty
    evidence_ids: list[NonEmpty] = Field(min_length=2)


class ComparisonTarget(QualityModel):
    dimension: NonEmpty
    paper_facts: list[PaperFact] = Field(min_length=2)
    expected_differences: list[ExpectedDifference] = Field(min_length=1)


class AbstentionTarget(QualityModel):
    expected_abstention: StrictBool
    boundary_kind: Literal[
        "private_information",
        "unprepared_page",
        "caption_only",
        "answerable_control",
        "version_scope",
    ]
    assessment_scope: Literal[
        "prepared_corpus_only", "provided_caption_only", "specified_prepared_page"
    ]
    checked_corpus_snapshot_id: SHA256
    rationale: NonEmpty
    requested_page: Page | None = None


class QualityCase(QualityModel):
    case_id: Annotated[
        NonEmpty,
        Field(pattern=r"^rqv1-(claim|reviewer|comparison|abstention)-[a-z0-9-]+$"),
    ]
    task_type: TaskType
    paper_ids: list[NonEmpty] = Field(min_length=1)
    dimension: NonEmpty
    question: NonEmpty
    reference_answer: NonEmpty
    gold_evidence_ids: list[NonEmpty]
    source_locations: list[SourceLocation]
    expected_behavior: Literal[
        "answer",
        "review_pass",
        "review_reject",
        "compare",
        "abstain",
        "answer_with_limits",
    ]
    annotation_status: Literal["draft", "pending_review", "approved_gold", "rejected"]
    annotation_provenance: AnnotationProvenance
    fact_group_ids: list[NonEmpty] = Field(min_length=1)
    split: Split = "unassigned"
    usage_partition: Literal["benchmark_only", "model_optimization"] = "benchmark_only"
    reviewer: ReviewerTarget | None = None
    comparison: ComparisonTarget | None = None
    abstention: AbstentionTarget | None = None

    @model_validator(mode="after")
    def contract(self):
        for values in [self.paper_ids, self.gold_evidence_ids, self.fact_group_ids]:
            unique(values)
        if not self.case_id.startswith("rqv1-" + self.task_type + "-"):
            raise ValueError("Case ID and task type differ")
        expected_fields = {
            "reviewer": self.reviewer,
            "comparison": self.comparison,
            "abstention": self.abstention,
        }
        if any(
            (value is not None) != (self.task_type == name)
            for name, value in expected_fields.items()
        ):
            raise ValueError("Task-specific fields must match the task type")
        location_ids = [s.evidence_id for s in self.source_locations]
        unique(location_ids)
        if set(location_ids) != set(self.gold_evidence_ids):
            raise ValueError("Source locations must match gold evidence IDs")
        if self.task_type != "abstention" and not self.gold_evidence_ids:
            raise ValueError("Answer/review/comparison cases require original evidence")
        if self.task_type == "claim" and self.expected_behavior != "answer":
            raise ValueError("Claim case requires answer behavior")
        if self.reviewer:
            unique(self.reviewer.cited_evidence_ids)
            if not set(self.reviewer.cited_evidence_ids) <= set(self.gold_evidence_ids):
                raise ValueError("Reviewer example citations are outside case sources")
            expected = (
                "review_pass"
                if self.reviewer.expected_label == "passed"
                else "review_reject"
            )
            if self.expected_behavior != expected:
                raise ValueError("Reviewer label and expected behavior disagree")
        if self.comparison:
            papers = [f.paper_id for f in self.comparison.paper_facts]
            unique(papers)
            if (
                len(self.paper_ids) < 2
                or set(papers) != set(self.paper_ids)
                or self.expected_behavior != "compare"
                or self.dimension != self.comparison.dimension
            ):
                raise ValueError(
                    "Comparison needs one independent fact per target paper"
                )
            ids = {i for f in self.comparison.paper_facts for i in f.evidence_ids}
            if ids != set(self.gold_evidence_ids):
                raise ValueError("Comparison evidence must be the paper-fact union")
            for fact in self.comparison.paper_facts:
                unique(fact.evidence_ids)
                unique(fact.fact_group_ids)
                if not set(fact.fact_group_ids) <= set(self.fact_group_ids):
                    raise ValueError("Independent fact lacks a case grouping marker")
            for diff in self.comparison.expected_differences:
                unique(diff.paper_ids)
                unique(diff.evidence_ids)
                if (
                    not set(diff.paper_ids) <= set(papers)
                    or not set(diff.evidence_ids) <= ids
                ):
                    raise ValueError(
                        "Expected difference cites an unknown party/source"
                    )
        if self.abstention:
            expected = (
                "abstain"
                if self.abstention.expected_abstention
                else "answer_with_limits"
            )
            if self.expected_behavior != expected:
                raise ValueError("Abstention decision and expected behavior disagree")
            if not self.abstention.expected_abstention and not self.gold_evidence_ids:
                raise ValueError("Answerable abstention controls require evidence")
        if self.annotation_status == "approved_gold":
            reviews = self.annotation_provenance.human_reviews
            if (
                not reviews
                or reviews[-1].verdict != "approved"
                or self.split == "unassigned"
                or self.usage_partition != "benchmark_only"
            ):
                raise ValueError(
                    "Approved Gold requires human approval and an evaluation split"
                )
            last = reviews[-1]
            if set(last.checked_evidence_ids) != set(self.gold_evidence_ids) or (
                self.abstention and not last.checked_boundary_scope
            ):
                raise ValueError(
                    "Gold review must cover all sources and boundary conditions"
                )
        elif self.annotation_status == "rejected":
            reviews = self.annotation_provenance.human_reviews
            if not reviews or reviews[-1].verdict != "rejected":
                raise ValueError(
                    "Rejected annotation requires a human rejection record"
                )
        return self


class CaseFile(QualityModel):
    filename: NonEmpty
    sha256: SHA256
    count: Count


class QualityManifest(QualityModel):
    schema_version: Literal[1] = 1
    dataset_version: Literal["research_quality_v1"]
    prepared_at: datetime
    stage_e_tag: Literal["stage-e-freeze-v1"]
    stage_e_commit_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    corpus_snapshot_id: SHA256
    registered_paper_ids: list[NonEmpty]
    purpose: Literal["research_quality_evaluation_only"]
    forbidden_uses: list[NonEmpty]
    target_counts: dict[TaskType, Count]
    case_files: dict[TaskType, CaseFile]
    notes: list[NonEmpty]


class QualityDataset(QualityModel):
    manifest: QualityManifest
    cases: list[QualityCase]


class QualityPrediction(QualityModel):
    case_id: NonEmpty
    prediction_id: NonEmpty
    system: NonEmpty
    answer: NonEmpty | None = None
    reviewer_label: Literal["passed", "rejected"] | None = None
    abstained: StrictBool | None = None


class ComparisonRubric(QualityModel):
    fact_accuracy: RubricScore
    difference_correctness: RubricScore
    evidence_grounding: RubricScore
    scope_completeness: RubricScore


class HumanAssessment(QualityModel):
    case_id: NonEmpty
    case_sha256: SHA256
    prediction_sha256: SHA256
    assessor: NonEmpty
    assessed_at: datetime
    method: Literal["human_semantic_review"]
    semantic_correct: StrictBool
    comparison_rubric: ComparisonRubric | None = None
    notes: NonEmpty

    @model_validator(mode="after")
    def dated(self):
        if self.assessed_at.tzinfo is None:
            raise ValueError("Human assessment timestamp requires a timezone")
        return self


def record_sha256(record):
    return canonical_hash(record.model_dump(mode="json"))
