"""候选标注的数据契约；静态合法不等于人工已批准 Ground Truth。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

NonEmpty = Annotated[StrictStr, Field(min_length=1, pattern=r"\S")]
Page = Annotated[StrictInt, Field(gt=0)]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Category = Literal["text_qa", "figure_qa", "cross_paper", "insufficient"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceReference(Record):
    evidence_id: NonEmpty
    paper_id: NonEmpty
    page: Page
    figure_id: NonEmpty | None = None


class FigureTarget(Record):
    paper_id: NonEmpty
    page: Page
    figure_id: NonEmpty


class AnswerPoint(Record):
    text: NonEmpty
    supporting_evidence_ids: list[NonEmpty]


class RegressionRecord(Record):
    question: NonEmpty
    source_artifact: Annotated[StrictStr, Field(pattern=r"^[^/\\]+\.json$")]
    source_sha256: Digest
    observed_status: Literal["step_exhaustion"]
    observed_tool_calls: StrictInt = Field(ge=0, le=6)
    observed_model_calls: StrictInt = Field(ge=0)
    observed_vlm_calls: StrictInt = Field(ge=0)
    total_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    backend: NonEmpty
    notes: list[NonEmpty] = Field(min_length=1)


class EvaluationCase(Record):
    case_id: Annotated[
        StrictStr,
        Field(pattern=r"^(dev|test)-(text|figure|cross|insufficient)-[0-9]{2}$"),
    ]
    category: Category
    question: NonEmpty
    paper_ids: list[NonEmpty] = Field(min_length=1)
    relevant_evidence_ids: list[NonEmpty]
    evidence_references: list[EvidenceReference]
    target_figures: list[FigureTarget] = Field(default_factory=list)
    answer_points: list[AnswerPoint] = Field(min_length=1)
    expected_behavior: Literal[
        "answer", "reject_false_premise", "insufficient_evidence"
    ]
    must_not_claim: list[NonEmpty]
    annotation_status: Literal["pending_review", "approved", "rejected"] = (
        "pending_review"
    )
    reviewer: NonEmpty | None = None
    reviewed_at: datetime | None = None
    review_notes: list[NonEmpty] = Field(default_factory=list)
    regression: RegressionRecord | None = None

    @model_validator(mode="after")
    def check_annotation(self):
        if self.regression is not None and self.regression.question != self.question:
            raise ValueError("Regression question must preserve the original request")
        for values in [self.paper_ids, self.relevant_evidence_ids]:
            if len(values) != len(set(values)):
                raise ValueError("Duplicate paper or evidence IDs")
        reference_ids = [e.evidence_id for e in self.evidence_references]
        if len(reference_ids) != len(set(reference_ids)) or set(reference_ids) != set(
            self.relevant_evidence_ids
        ):
            raise ValueError(
                "Evidence references must match relevant evidence IDs exactly"
            )
        if self.category == "insufficient":
            if (
                self.expected_behavior != "insufficient_evidence"
                or not self.must_not_claim
            ):
                raise ValueError(
                    "Insufficient cases require explicit refusal behavior and forbidden claims"
                )
        elif self.expected_behavior == "insufficient_evidence":
            raise ValueError("Refusal behavior belongs to the insufficient category")
        else:
            if not self.relevant_evidence_ids:
                raise ValueError("Answerable cases require positive evidence")
            for point in self.answer_points:
                if not point.supporting_evidence_ids:
                    raise ValueError("Answerable points require evidence identities")
        for point in self.answer_points:
            if not set(point.supporting_evidence_ids).issubset(
                self.relevant_evidence_ids
            ):
                raise ValueError("Answer points cite evidence outside the case")
        if self.annotation_status in {"approved", "rejected"}:
            if self.reviewer is None or self.reviewed_at is None:
                raise ValueError("Human review outcome requires reviewer and timestamp")
        elif self.reviewer is not None or self.reviewed_at is not None:
            raise ValueError("Pending candidates must not imply a completed review")
        return self


class PaperSnapshot(Record):
    paper_id: NonEmpty
    pdf_sha256: Digest
    prepared_pages: list[Page] = Field(min_length=1)
    prepared_figure_ids: list[NonEmpty] = Field(min_length=1)


class EvaluationDataset(Record):
    schema_version: Literal[1] = 1
    split: Literal["dev", "test"]
    annotation_origin: Literal["coding_agent_candidates"] = "coding_agent_candidates"
    page_numbering: Literal["one-based PDF page index"] = "one-based PDF page index"
    corpus_snapshot: list[PaperSnapshot] = Field(min_length=1)
    notes: list[NonEmpty] = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)


class EvaluationRun(Record):
    """一个系统对一道题的运行结果；语义分数为 null 表示未审阅，而不是零分。"""

    run_id: NonEmpty
    case_id: NonEmpty
    split: Literal["dev", "test"]
    category: Category
    system: Literal["text_rag", "fixed_multimodal_rag", "multimodal_agent"]
    backend: Literal["fake", "real"]
    answer: str | None
    citations: list[EvidenceReference]
    retrieved_evidence_ids: list[NonEmpty]
    status: NonEmpty
    limitations: list[str]
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    model_calls: StrictInt = Field(ge=0)
    vlm_calls: StrictInt = Field(ge=0)
    tool_calls: StrictInt = Field(ge=0)
    llm_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    vlm_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    tool_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    task_success: bool | None = None
    task_success_source: Literal[
        "pending_review", "automatic_failure", "human_review"
    ] = "pending_review"
    citation_correctness: float | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False
    )
    citation_correctness_source: Literal[
        "pending_review", "not_applicable", "human_review"
    ] = "pending_review"
    citation_identity_valid: bool
    citation_relevance_precision: float | None = Field(default=None, ge=0, le=1)
    text_recall_at_k: float | None = Field(default=None, ge=0, le=1)
    text_recall_k: StrictInt | None = Field(default=None, gt=0)
    text_evidence_recall: float | None = Field(default=None, ge=0, le=1)
    figure_recall_at_k: float | None = Field(default=None, ge=0, le=1)
    figure_recall_k: StrictInt = Field(gt=0)
    figure_recall_protocol: Literal[
        "first_k_acquired_figures_not_ranked_image_search"
    ] = "first_k_acquired_figures_not_ranked_image_search"
    trace: list[dict] = Field(default_factory=list)
    llm_events: list[dict] = Field(default_factory=list)
    vlm_events: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class EvaluationReview(Record):
    run_id: NonEmpty
    reviewer: NonEmpty
    reviewed_at: datetime
    task_success: bool | None = None
    citation_correctness: float | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False
    )
    notes: list[str] = Field(default_factory=list)
