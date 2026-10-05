import json
import shutil
from pathlib import Path

import pytest

from app.evidence.corpus import load_paper_corpus
from app.evidence.smoke import load_smoke_questions

ROOT = Path(__file__).resolve().parents[3]


def test_three_papers_stable_text_figures_and_every_evidence_is_traceable():
    first = load_paper_corpus(ROOT / "data/papers")
    second = load_paper_corpus(ROOT / "data/papers")
    assert first.text_evidence == second.text_evidence
    assert first.figure_evidence == second.figure_evidence
    assert len(first.text_evidence) == 25
    assert len(first.figure_evidence) == 3
    assert len(first.locations) == 28
    for meta in first.metadata:
        assert first.paper_aliases[meta.paper_key] == meta.paper_id
        assert any(e.paper_id == meta.paper_id for e in first.text_evidence)
        assert sum(e.paper_id == meta.paper_id for e in first.figure_evidence) == 1
    for item in first.text_evidence:
        location = first.locate(item.evidence_id)
        assert location.paper_id == item.paper_id
        assert location.page == item.page
        assert item.text in location.page_text_path.read_text()
        assert location.pdf_path.is_file()
    for item in first.figure_evidence:
        location = first.locate(item.evidence_id)
        assert location.figure_id == item.figure_id
        assert location.image_path == Path(item.asset_path)
        assert location.caption == item.caption
        assert location.page == item.page


@pytest.mark.parametrize("tamper", ["page", "pdf", "context", "escape"])
def test_invalid_provenance_rejected(tmp_path, tamper):
    paper = tmp_path / "papers/blip"
    shutil.copytree(ROOT / "data/papers/blip", paper)
    metadata = json.loads((paper / "metadata.json").read_text())
    if tamper == "page":
        (paper / metadata["pages"][0]["path"]).write_text("changed source")
    elif tamper == "pdf":
        (paper / "paper.pdf").write_bytes(b"wrong PDF version")
    elif tamper == "context":
        metadata["figures"][0]["related_text"] = "invented paper context"
    else:
        metadata["pages"][0]["path"] = "../../outside.txt"
    (paper / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        load_paper_corpus(tmp_path / "papers")


def test_smoke_questions_cover_two_per_paper_and_two_cross_paper_questions():
    corpus = load_paper_corpus(ROOT / "data/papers")
    questions = load_smoke_questions(ROOT / "data/smoke/day2_queries.json", corpus)
    assert len(questions) == 8
    for paper in ["clip", "blip", "flamingo"]:
        assert (
            sum(
                len(q.requests) == 1 and q.requests[0].paper_key == paper
                for q in questions
            )
            == 2
        )
    cross = [q for q in questions if len(q.requests) > 1]
    assert len(cross) == 2
    assert all(len({r.paper_key for r in q.requests}) == 2 for q in cross)
