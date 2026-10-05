"""One extraction mechanism, manually selected coordinates per paper."""

import hashlib
import shutil
from pathlib import Path

from app.evidence.fixtures import (
    PaperFixtureConfig,
    PaperMetadata,
    PreparedFigure,
    PreparedPage,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_fixture(
    config: PaperFixtureConfig, pdf_path: Path, output: Path
) -> PaperMetadata:
    if file_sha256(pdf_path) != config.pdf_sha256:
        raise ValueError("PDF SHA256 mismatch: coordinates require the pinned version")
    import pymupdf

    # Validate all page bounds before creating any output.
    with pymupdf.open(pdf_path) as doc:
        page_rectangles = [(p.page, r) for p in config.page_regions for r in p.regions]
        page_rectangles += [
            (f.page, r) for f in config.figures for r in [f.crop, f.caption_region]
        ]
        for page, rectangle in page_rectangles:
            if page > len(doc) or not doc[page - 1].rect.contains(
                pymupdf.Rect(rectangle)
            ):
                raise ValueError("Configured page or rectangle is outside this PDF")
        output.mkdir(parents=True, exist_ok=True)
        (output / "pages").mkdir(exist_ok=True)
        (output / "figures").mkdir(exist_ok=True)
        if pdf_path.resolve() != (output / "paper.pdf").resolve():
            shutil.copyfile(pdf_path, output / "paper.pdf")
        selected, pages, figures = {}, [], []
        for entry in config.page_regions:
            blocks = []
            for rectangle in entry.regions:
                for block in doc[entry.page - 1].get_text(
                    "blocks", clip=pymupdf.Rect(rectangle)
                ):
                    if block[6] == 0 and block[4].strip():
                        blocks.append(" ".join(block[4].split()))
            if not blocks:
                raise ValueError("Selected page has no text")
            selected[entry.page] = "\n\n".join(blocks)
            path = f"pages/page-{entry.page:03}.txt"
            (output / path).write_text(selected[entry.page] + "\n", encoding="utf-8")
            pages.append(
                PreparedPage(
                    **entry.model_dump(), path=path, sha256=file_sha256(output / path)
                )
            )
        for index, figure in enumerate(config.figures):
            page = doc[figure.page - 1]
            path = f"figures/figure-{index + 1:03}.png"
            page.get_pixmap(
                matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(figure.crop)
            ).save(output / path)
            caption = " ".join(
                page.get_text("text", clip=pymupdf.Rect(figure.caption_region)).split()
            )
            figures.append(
                PreparedFigure(
                    **figure.model_dump(),
                    asset_path=path,
                    asset_sha256=file_sha256(output / path),
                    caption=caption,
                    related_text="\n\n".join(selected[p] for p in figure.related_pages),
                )
            )
        metadata = PaperMetadata(
            **config.model_dump(exclude={"page_regions", "figures"}),
            pages=pages,
            figures=figures,
        )
        (output / "metadata.json").write_text(
            metadata.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return metadata
