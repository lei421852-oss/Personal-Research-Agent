import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evidence.fixtures import PaperFixtureConfig, PaperMetadata
from app.evidence.preparation import prepare_fixture

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("paper", ["clip", "blip", "flamingo"])
def test_three_configs_follow_same_contract(paper):
    config = PaperFixtureConfig.model_validate_json(
        (ROOT / "data/fixture_configs" / f"{paper}.json").read_text()
    )
    assert config.paper_key == paper
    assert len(config.figures) == 1
    assert len(config.smoke_queries) >= 2


@pytest.mark.parametrize(
    "rectangle", [[1, 1, 0, 2], [-1, 0, 2, 2], [0, 0, float("inf"), 2]]
)
def test_invalid_rectangles_rejected(rectangle):
    config = json.loads((ROOT / "data/fixture_configs/blip.json").read_text())
    config["figures"][0]["crop"] = rectangle
    with pytest.raises(ValidationError):
        PaperFixtureConfig.model_validate(config)


def test_unknown_context_page_rejected():
    config = json.loads((ROOT / "data/fixture_configs/blip.json").read_text())
    config["figures"][0]["related_pages"] = [999]
    with pytest.raises(ValidationError):
        PaperFixtureConfig.model_validate(config)


def test_wrong_pdf_version_fails_before_writing(tmp_path):
    config = PaperFixtureConfig.model_validate_json(
        (ROOT / "data/fixture_configs/blip.json").read_text()
    )
    pdf = tmp_path / "wrong.pdf"
    pdf.write_bytes(b"wrong version")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="SHA256"):
        prepare_fixture(config, pdf, output)
    assert not output.exists()


def test_blip_uses_common_metadata_schema():
    metadata = PaperMetadata.model_validate_json(
        (ROOT / "data/papers/blip/metadata.json").read_text()
    )
    assert metadata.figures[0].page == 4
    assert metadata.pages[0].regions
