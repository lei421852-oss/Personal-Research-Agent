"""Load the explicitly prepared page-level corpus, without guessing pages."""

import json
from pathlib import Path

from app.evidence.models import TextEvidence, make_evidence_id


def load_text_corpus(paper_dir: Path) -> list[TextEvidence]:
    metadata = json.loads((paper_dir / "metadata.json").read_text(encoding="utf-8"))
    evidence = []
    for entry in metadata["pages"]:
        page = entry["page"]
        text = (paper_dir / entry["path"]).read_text(encoding="utf-8").strip()
        # Fixed paragraph boundaries within each PDF page; never cross a page.
        for paragraph in text.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph:
                evidence.append(
                    TextEvidence(
                        evidence_id=make_evidence_id(
                            metadata["paper_id"], page, "text", paragraph
                        ),
                        paper_id=metadata["paper_id"],
                        page=page,
                        text=paragraph,
                    )
                )
    return list({item.evidence_id: item for item in evidence}.values())
