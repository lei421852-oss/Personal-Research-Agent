"""真实 PDF 的来源校验和篡改拒绝，不调用任何模型。"""

import json
import shutil
from pathlib import Path

import pytest

from app.evidence.corpus import _load_paper, load_paper_corpus
from app.evidence.fixtures import PaperMetadata
from app.evidence.preparation import prepare_fixture
from app.research.corpus_validation import (
    PreparedEvidence,
    SourceManifest,
    pdf_blocks,
    write_provenance,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def paper(tmp_path):
    target = tmp_path / "llava"
    shutil.copytree(ROOT / "data/research_papers/llava", target)
    return target


def rewrite(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def test_all_manifests_and_projections_are_verified():
    corpus = load_paper_corpus(
        ROOT / "data/papers", additional_papers_dir=ROOT / "data/research_papers"
    )
    assert len(corpus.metadata) == 5
    assert len(corpus.text_evidence) == 46 and len(corpus.figure_evidence) == 5
    for meta in corpus.metadata:
        root = (
            ROOT
            / (
                "data/research_papers"
                if meta.paper_key in {"blip2", "llava"}
                else "data/papers"
            )
            / meta.paper_key
        )
        manifest = SourceManifest.model_validate_json(
            (root / "source_manifest.json").read_text()
        )
        assert manifest.paper_id == meta.paper_id
        assert manifest.source_sha256 == meta.pdf_sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("paper_id", "foreign-2304.08485v1"),
        ("source_sha256", "0" * 64),
        ("source_version", "2304.08485v2"),
        ("extraction_config_sha256", "0" * 64),
        ("page_count", 999),
    ],
)
def test_source_manifest_identity_version_and_hash_reject(paper, field, value):
    rewrite(paper / "source_manifest.json", lambda v: v.update({field: value}))
    with pytest.raises(ValueError):
        _load_paper(paper)


def test_actual_source_hash_mismatch(paper):
    with (paper / "paper.pdf").open("ab") as stream:
        stream.write(b"changed-source")
    with pytest.raises(ValueError, match="hash mismatch"):
        _load_paper(paper)


@pytest.mark.parametrize(
    "field,value",
    [
        ("paper_id", "foreign-paper"),
        ("page_number", 99),
        ("content", "Profile-only fabricated fact."),
        ("source_document_hash", "0" * 64),
    ],
)
def test_prepared_row_cannot_rewrite_source(paper, field, value):
    path = paper / "prepared_evidence.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0][field] = value
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError):
        _load_paper(paper)


def test_duplicate_evidence_is_rejected(paper):
    path = paper / "prepared_evidence.jsonl"
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="identity_mismatch"):
        _load_paper(paper)


def test_missing_manifest_fails_closed(paper):
    (paper / "source_manifest.json").unlink()
    with pytest.raises(FileNotFoundError):
        _load_paper(paper)


def test_preparation_stable_ids_and_byte_identical_reimport(paper):
    write_provenance(paper)
    before = {
        name: (paper / name).read_bytes()
        for name in ["source_manifest.json", "prepared_evidence.jsonl"]
    }
    write_provenance(paper)
    assert before == {name: (paper / name).read_bytes() for name in before}
    assert len(_load_paper(paper)[1]) == 10


def test_pinned_preparation_recreates_original_regions(tmp_path):
    from app.evidence.fixtures import PaperFixtureConfig

    data = json.loads((ROOT / "data/fixture_configs/llava.json").read_text())
    config = PaperFixtureConfig.model_validate(data["fixture"])
    target = tmp_path / "llava"
    first = prepare_fixture(
        config, ROOT / "data/research_papers/llava/paper.pdf", target
    )
    second = prepare_fixture(config, target / "paper.pdf", target)
    assert first == second
    write_provenance(target, section_titles=data["section_titles"])
    assert {e.evidence_id for e in _load_paper(target)[1]} == {
        e.evidence_id for e in _load_paper(ROOT / "data/research_papers/llava")[1]
    }


def test_double_column_reading_order_anomaly_is_not_silently_sorted():
    class Page:
        def get_text(self, *args, **kwargs):
            return [
                (10, 80, 30, 100, "Lower paragraph", 0, 0),
                (10, 10, 30, 30, "Upper paragraph", 1, 0),
            ]

    with pytest.raises(ValueError, match="reading_order_anomaly"):
        pdf_blocks(Page(), [(0, 0, 200, 200)])


def test_overlapping_regions_do_not_duplicate_paragraphs():
    class Page:
        def get_text(self, *args, **kwargs):
            return [(10, 10, 30, 30, "Same paragraph", 0, 0)]

    with pytest.raises(ValueError, match="overlapping_regions"):
        pdf_blocks(Page(), [(0, 0, 50, 50)] * 2)


def test_printed_page_mapping_is_verified(paper):
    manifest = SourceManifest.model_validate_json(
        (paper / "source_manifest.json").read_text()
    )
    assert manifest.page_mapping["4"] == "4"
    rewrite(
        paper / "source_manifest.json", lambda v: v["page_mapping"].update({"4": "999"})
    )
    with pytest.raises(ValueError, match="printed_page_label"):
        _load_paper(paper)


def test_caption_pixel_and_linked_text_provenance(paper):
    rows = [
        PreparedEvidence.model_validate_json(line)
        for line in (paper / "prepared_evidence.jsonl").read_text().splitlines()
    ]
    figure = next(r for r in rows if r.evidence_type == "figure")
    assert figure.caption.startswith("Figure 1:") and figure.pixels_available
    assert all(not r.pixels_available for r in rows if r.evidence_type == "text")
    assert set(figure.associated_text_evidence_ids) <= {
        r.evidence_id
        for r in rows
        if r.paper_id == figure.paper_id and r.evidence_type == "text"
    }
    path = paper / "prepared_evidence.jsonl"
    values = [json.loads(line) for line in path.read_text().splitlines()]
    values[-1]["associated_text_evidence_ids"] = ["foreign-evidence"]
    path.write_text("".join(json.dumps(row) + "\n" for row in values))
    with pytest.raises(ValueError, match="provenance_mismatch"):
        _load_paper(paper)


def test_wrong_caption_number_is_rejected(paper):
    rewrite(
        paper / "metadata.json",
        lambda v: v["figures"][0].update({"caption": "Figure 99: Wrong image."}),
    )
    meta = PaperMetadata.model_validate_json((paper / "metadata.json").read_text())
    with pytest.raises(ValueError, match="figure_caption_mismatch"):
        from app.research.corpus_validation import build_prepared

        build_prepared(paper, meta)


def test_corrupted_pixels_are_rejected(paper):
    (paper / "figures/figure-001.png").write_bytes(b"wrong-image")
    with pytest.raises(ValueError, match="hash mismatch"):
        _load_paper(paper)
