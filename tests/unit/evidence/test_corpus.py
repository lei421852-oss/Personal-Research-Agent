import hashlib
import json
from pathlib import Path

from app.evidence.corpus import load_text_corpus

ROOT = Path(__file__).resolve().parents[3]


def test_fixed_blip_corpus_ids_pages_and_source_are_reproducible():
    paper_dir = ROOT / "data/papers/blip"
    first, second = load_text_corpus(paper_dir), load_text_corpus(paper_dir)
    assert first == second
    assert {item.page for item in first} == {2, 3, 4}
    assert {item.paper_id for item in first} == {"blip-2201.12086v2"}
    assert len({item.evidence_id for item in first}) == len(first)
    metadata = json.loads((paper_dir / "metadata.json").read_text())
    assert (
        hashlib.sha256((paper_dir / "paper.pdf").read_bytes()).hexdigest()
        == metadata["pdf_sha256"]
    )
    assert (paper_dir / metadata["figures"][0]["asset_path"]).is_file()
    assert metadata["figures"][0]["page"] == 4
    assert "Figure 3" in metadata["figures"][0]["caption"]
