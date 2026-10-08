"""覆盖协议结构测试，不将模拟策略的自报结论当作语义正确性证明。"""

import pytest
from pydantic import ValidationError

from app.research.coverage_models import (
    ASPECTS_BY_DIMENSION,
    DEFAULT_ASPECTS,
    BatchResolution,
    CoverageAspect,
    CoverageFact,
    CoverageResolution,
    CoverageStage,
)
from app.research.models import MAX_DIMENSIONS


def fact(**overrides):
    return CoverageFact.model_validate(
        {
            "subject_paper_id": "sample-paper",
            "dimension_id": "language_model",
            "text": "The documented language backbone remains frozen.",
            "evidence_ids": ["ev-local"],
        }
        | overrides
    )


def aspect(aspect_id="model_family", **overrides):
    return CoverageAspect.model_validate(
        {"aspect_id": aspect_id, "status": "covered", "fact": fact()} | overrides
    )


def resolution(**overrides):
    return CoverageResolution.model_validate(
        {
            "requirement_id": "req-local",
            "status": "supported",
            "coverage": [aspect()],
        }
        | overrides
    )


def test_dimension_aspects_are_generic_and_other_dimensions_have_fallback():
    assert len(ASPECTS_BY_DIMENSION) == 5
    assert ASPECTS_BY_DIMENSION["vision_encoder"] == (
        "type",
        "configuration",
        "feature_output",
    )
    assert ASPECTS_BY_DIMENSION["vision_language_bridge"] == (
        "connector",
        "visual_transform",
        "language_interface",
    )
    assert ASPECTS_BY_DIMENSION["frozen_trainable_scope"] == (
        "training_stages",
        "frozen_modules",
        "updated_modules",
    )
    assert ASPECTS_BY_DIMENSION["training_objective"] == (
        "training_stages",
        "losses",
        "optimization_targets",
    )
    assert ASPECTS_BY_DIMENSION["language_model"] == (
        "model_family",
        "architecture",
        "update_state",
    )
    assert ASPECTS_BY_DIMENSION.get("custom_dimension", DEFAULT_ASPECTS) == (
        "requested_facts",
    )


def test_fact_ids_are_deduplicated_without_reordering():
    item = fact(evidence_ids=["ev-b", "ev-a", "ev-b", "ev-a"])
    assert item.evidence_ids == ["ev-b", "ev-a"]


@pytest.mark.parametrize("identities", [[], [""], ["  "], [123]])
def test_fact_requires_nonempty_string_evidence_ids(identities):
    with pytest.raises(ValidationError):
        fact(evidence_ids=identities)


@pytest.mark.parametrize("field", ["subject_paper_id", "text", "dimension_id"])
def test_fact_requires_nonempty_identity_and_text(field):
    with pytest.raises(ValidationError):
        fact(**{field: "  "})


@pytest.mark.parametrize("kind", ["exclusive", "limited_absence"])
def test_bounded_assertions_require_nonempty_supporting_quote(kind):
    with pytest.raises(ValidationError):
        fact(assertion_kind=kind)
    with pytest.raises(ValidationError):
        fact(assertion_kind=kind, support_quote="  ")


def test_exclusive_only_word_is_allowed_when_quote_is_explicit():
    text = "Only the connection module is updated in the documented phase."
    item = fact(text=text, assertion_kind="exclusive", support_quote=text)
    assert item.text == text
    assert item.assertion_kind == "exclusive"


def test_limited_absence_has_explicit_basis_and_descriptive_quote_is_optional():
    assert fact().support_quote is None
    item = fact(
        text="The supplied passage does not specify the backbone variant.",
        assertion_kind="limited_absence",
        support_quote="We use a frozen language model.",
    )
    assert item.support_quote == "We use a frozen language model."


@pytest.mark.parametrize("status", ["covered", "not_applicable"])
def test_covered_and_not_applicable_require_fact(status):
    with pytest.raises(ValidationError):
        CoverageAspect(aspect_id="architecture", status=status)
    assert aspect(status=status).fact is not None


def test_missing_cannot_carry_fact():
    with pytest.raises(ValidationError):
        aspect(status="missing")
    item = CoverageAspect(aspect_id="model_family", status="missing")
    assert item.fact is None


def test_resolution_preserves_explicit_gap_without_implying_completeness():
    item = resolution(
        coverage=[CoverageAspect(aspect_id="model_family", status="missing")]
    )
    # 是否允许提交 supported 由 Worker 检查；模型先保留原始提议用于反馈。
    assert item.status == "supported"
    assert item.coverage[0].status == "missing"


def test_empty_coverage_is_structurally_valid_for_actionable_worker_feedback():
    assert resolution(coverage=[]).coverage == []
    assert resolution(coverage=[]).stages == []


def test_training_stages_are_named_without_fixed_stage_number():
    stage = CoverageStage(
        stage_id="joint_alignment",
        name="Joint alignment phase",
        aspects=[aspect("updated_modules")],
    )
    item = resolution(stages=[stage])
    assert item.stages[0].stage_id == "joint_alignment"
    assert item.stages[0].name == "Joint alignment phase"


def test_duplicate_coverage_aspect_is_rejected():
    with pytest.raises(ValidationError, match="Duplicate coverage aspect_id"):
        resolution(coverage=[aspect(), aspect()])


def test_duplicate_stage_aspect_is_rejected():
    with pytest.raises(ValidationError, match="Duplicate stage aspect_id"):
        CoverageStage(stage_id="alignment", name="Alignment", aspects=[aspect()] * 2)


def test_duplicate_stage_id_is_rejected():
    stage = CoverageStage(stage_id="alignment", name="Alignment", aspects=[aspect()])
    with pytest.raises(ValidationError, match="Duplicate coverage stage_id"):
        resolution(stages=[stage, stage])


@pytest.mark.parametrize("status", ["complete", "partial_success", "failed"])
def test_resolution_status_does_not_change_existing_completion_vocabulary(status):
    with pytest.raises(ValidationError):
        resolution(status=status)


@pytest.mark.parametrize(
    "extra",
    [
        {"claim": "An unstructured final claim."},
        {"evidence_ids": ["ev-local"]},
        {"reasoning": "Private model reasoning."},
    ],
)
def test_resolution_has_no_free_claim_citation_or_reasoning_field(extra):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        resolution(**extra)


def test_batch_rejects_duplicate_requirement_identity():
    with pytest.raises(ValidationError, match="Duplicate coverage requirement_id"):
        BatchResolution(resolutions=[resolution(), resolution()])


def test_batch_respects_existing_dimension_count_bound():
    with pytest.raises(ValidationError):
        BatchResolution(resolutions=[])
    with pytest.raises(ValidationError):
        BatchResolution(
            resolutions=[
                resolution(requirement_id=f"req-{index}")
                for index in range(MAX_DIMENSIONS + 1)
            ]
        )
    batch = BatchResolution(
        resolutions=[
            resolution(requirement_id=f"req-{index}") for index in range(MAX_DIMENSIONS)
        ]
    )
    assert len(batch.resolutions) == MAX_DIMENSIONS


@pytest.mark.parametrize(
    "item,field,value",
    [
        (fact(), "text", "Changed claim"),
        (aspect(), "status", "missing"),
        (resolution(), "status", "insufficient_evidence"),
        (
            CoverageStage(stage_id="alignment", name="Alignment", aspects=[]),
            "name",
            "Changed phase",
        ),
        (BatchResolution(resolutions=[resolution()]), "resolutions", []),
    ],
)
def test_models_are_frozen(item, field, value):
    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(item, field, value)


def test_nested_unknown_fields_are_rejected():
    payload = resolution().model_dump()
    payload["coverage"][0]["fact"]["external_answer"] = "Unsupported content"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CoverageResolution.model_validate(payload)
