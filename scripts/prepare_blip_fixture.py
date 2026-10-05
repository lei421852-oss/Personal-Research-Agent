"""Reproduce the manually selected BLIP v2 pages/figure; no figure detection.

Usage: python scripts/prepare_blip_fixture.py --pdf /path/to/2201.12086v2.pdf
Requires the optional data extra. Does not download anything or run models.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_ID = "blip-2201.12086v2"
PDF_SHA256 = "63e0335cdd57c3d92b907b89c93d10befdd29b8aeed108091bbde90c121ed97a"


def prepare(pdf_path: Path, output: Path) -> None:
    import pymupdf

    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if digest != PDF_SHA256:
        raise ValueError(
            "Expected the pinned BLIP arXiv v2 PDF; crop/page coordinates are version-specific"
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "pages").mkdir(exist_ok=True)
    (output / "figures").mkdir(exist_ok=True)
    target = output / "paper.pdf"
    if pdf_path.resolve() != target.resolve():
        shutil.copyfile(pdf_path, target)
    doc = pymupdf.open(target)
    selected = {}
    # Explicitly chosen source rectangles; keep original PDF page identity.
    regions = {
        2: [(50, 260, 545, 343)],
        3: [(50, 225, 295, 577), (50, 598, 295, 716), (303, 65, 546, 430)],
        4: [(50, 272, 295, 523)],
    }
    for page, rectangles in regions.items():
        blocks = []
        for rectangle in rectangles:
            # Original PDF text blocks, joined without crossing a page.
            for block in doc[page - 1].get_text("blocks", clip=pymupdf.Rect(rectangle)):
                if block[6] == 0 and block[4].strip():
                    blocks.append(" ".join(block[4].split()))
        selected[page] = blocks
        (output / "pages" / f"page-{page:03}.txt").write_text(
            "\n\n".join(blocks) + "\n", encoding="utf-8"
        )
    page = doc[3]
    crop = (55, 62, 545, 225)  # Figure 3 only, caption supplied separately.
    page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(crop)).save(
        output / "figures" / "figure-3.png"
    )
    caption = " ".join(
        page.get_text("text", clip=pymupdf.Rect(50, 230, 545, 267)).split()
    )
    metadata = {
        "paper_id": PAPER_ID,
        "title": "BLIP: Bootstrapping Language-Image Pre-training for Unified Vision-Language Understanding and Generation",
        "source_url": "https://arxiv.org/abs/2201.12086v2",
        "pdf_url": "https://arxiv.org/pdf/2201.12086v2",
        "pdf_sha256": digest,
        "page_numbering": "one-based PDF page index",
        "preparation": "Manually selected page regions and fixed figure crop, not a general PDF parser.",
        "pages": [
            {"page": page, "path": f"pages/page-{page:03}.txt"} for page in regions
        ],
        "figure": {
            "figure_id": "Figure 3",
            "page": 4,
            "asset_path": "figures/figure-3.png",
            "caption": caption,
            "related_text": "\n\n".join(selected[4]),
            "crop_pdf_points": crop,
        },
        "smoke_queries": [
            {
                "query": "How does CapFilt handle noisy image-text pairs?",
                "expected_pages": [4],
                "expected_terms": ["filter"],
            },
            {
                "query": "What are the three vision-language objectives used by BLIP?",
                "expected_pages": [2, 3],
                "expected_terms": ["ITC", "ITM", "LM"],
            },
            {
                "query": "What is the role of the captioner in CapFilt?",
                "expected_pages": [4],
                "expected_terms": ["caption"],
            },
        ],
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    doc.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/papers/blip")
    args = parser.parse_args()
    prepare(args.pdf, args.output)
