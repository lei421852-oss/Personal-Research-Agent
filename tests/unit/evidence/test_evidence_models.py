import pytest
from pydantic import ValidationError

from app.evidence.models import (
    FigureEvidence,
    RetrievedEvidence,
    TextEvidence,
    make_evidence_id,
)


def text_evidence(**overrides):
    fields = {
        "evidence_id": "ev_test",
        "paper_id": "blip-v2",
        "page": 3,
        "text": "CapFilt context",
    }
    return TextEvidence(**(fields | overrides))


def test_text_and_figure_construct_without_forcing_figure_fields_on_text():
    text = text_evidence()
    assert text.modality == "text"
    assert "asset_path" not in text.model_dump()
    figure = FigureEvidence(
        evidence_id="ev_figure",
        paper_id="blip-v2",
        page=3,
        text="Related text",
        figure_id="Figure 2",
        caption="CapFilt",
        asset_path="figure2.png",
    )
    assert figure.modality == "figure"


@pytest.mark.parametrize("page", [0, -1, True, 1.5, "3"])
def test_invalid_page_rejected(page):
    with pytest.raises(ValidationError):
        text_evidence(page=page)


@pytest.mark.parametrize("modality", ["video", "figure"])
def test_invalid_text_modality_rejected(modality):
    with pytest.raises(ValidationError):
        text_evidence(modality=modality)


def test_stable_identity_and_different_locations():
    args = ("blip-v2", 3, "text", "paragraph-1")
    assert make_evidence_id(*args) == make_evidence_id(*args)
    assert make_evidence_id(*args) != make_evidence_id(
        "blip-v2", 4, "text", "paragraph-1"
    )


def test_retrieved_evidence_does_not_modify_original():
    evidence = text_evidence()
    before = evidence.model_dump()
    first = RetrievedEvidence(evidence=evidence, retrieval_score=0.9)
    second = RetrievedEvidence(evidence=evidence, retrieval_score=0.2)
    assert evidence.model_dump() == before
    assert first.retrieval_score != second.retrieval_score
    assert "retrieval_score" not in evidence.model_dump()
    with pytest.raises(ValidationError):
        evidence.page = 9
