"""引用、计划范围及多论文证据完整性是确定性约束。"""

import pytest
from test_synthesis_input import proposal, source

from app.research.synthesis_validator import (
    SynthesisOutputError,
    SynthesisOutputValidator,
)


def test_valid_complete_structure_passes():
    value = source()
    assert (
        len(SynthesisOutputValidator().validate(proposal(value), value).sections) == 2
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("claim_id", "claim-fake"),
        ("evidence_ids", ["ev-fake"]),
        ("paper_id", "paper-c"),
        ("dimension_id", "training"),
        ("requirement_id", "req-fake"),
    ],
)
def test_illegal_identity_or_citation_is_rejected(field, value):
    original = source()
    output = proposal(original)
    output["sections"][0]["cells"][0][field] = value
    with pytest.raises(SynthesisOutputError):
        SynthesisOutputValidator().validate(output, original)


def test_evidence_from_another_verified_claim_is_also_illegal():
    original = source()
    output = proposal(original)
    output["sections"][0]["cells"][0]["evidence_ids"] = original.verified_claims[
        1
    ].evidence_ids
    with pytest.raises(SynthesisOutputError) as error:
        SynthesisOutputValidator().validate(output, original)
    assert error.value.citation


def test_missing_cell_cannot_be_upgraded_or_given_facts():
    original = source(statuses={("paper-a", "architecture"): "insufficient_evidence"})
    output = proposal(original)
    output["sections"][0]["cells"][0]["status"] = "verified"
    with pytest.raises(SynthesisOutputError, match="invalid_cell_status"):
        SynthesisOutputValidator().validate(output, original)


def test_claim_text_or_free_technical_summary_cannot_be_inserted():
    original = source()
    for location in ["cell", "root"]:
        output = proposal(original)
        target = output["sections"][0]["cells"][0] if location == "cell" else output
        target["claim" if location == "cell" else "summary"] = (
            "Fabricated technical assertion"
        )
        with pytest.raises(SynthesisOutputError, match="invalid_structure"):
            SynthesisOutputValidator().validate(output, original)


def comparison(value):
    claims = [c for c in value.verified_claims if c.dimension_id == "architecture"][:2]
    return {
        "paper_ids": [c.paper_id for c in claims],
        "claim_ids": [c.claim_id for c in claims],
        "evidence_ids": [e for c in claims for e in c.evidence_ids],
    }


def test_legitimate_same_dimension_side_by_side_comparison_passes():
    value = source()
    output = proposal(value)
    output["sections"][0]["comparisons"] = [comparison(value)]
    result = SynthesisOutputValidator().validate(output, value)
    assert result.sections[0].comparisons[0].relation == "side_by_side"


@pytest.mark.parametrize(
    "mode",
    ["missing_party", "new_paper", "new_claim", "wrong_dimension", "new_evidence"],
)
def test_incomplete_or_out_of_scope_comparison_is_rejected(mode):
    value = source()
    output = proposal(value)
    item = comparison(value)
    if mode == "missing_party":
        item["paper_ids"].append("paper-c")
    elif mode == "new_paper":
        item["paper_ids"][1] = "paper-unknown"
    elif mode == "new_claim":
        item["claim_ids"][1] = "claim-missing"
    elif mode == "wrong_dimension":
        item["claim_ids"][1] = value.verified_claims[1].claim_id
    else:
        item["evidence_ids"].append("ev-missing")
    output["sections"][0]["comparisons"] = [item]
    with pytest.raises(SynthesisOutputError):
        SynthesisOutputValidator().validate(output, value)


@pytest.mark.parametrize(
    "mode", ["missing_cell", "duplicate_cell", "missing_section", "duplicate_section"]
)
def test_each_planned_cell_and_dimension_must_appear_exactly_once(mode):
    value = source()
    output = proposal(value)
    if mode == "missing_cell":
        output["sections"][0]["cells"].pop()
    elif mode == "duplicate_cell":
        output["sections"][0]["cells"].append(output["sections"][0]["cells"][0])
    elif mode == "missing_section":
        output["sections"].pop()
    else:
        output["sections"].append(output["sections"][0])
    with pytest.raises(SynthesisOutputError):
        SynthesisOutputValidator().validate(output, value)
