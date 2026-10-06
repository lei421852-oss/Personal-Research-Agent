"""人工论文 fixture 的统一版本契约。"""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evidence.models import NonEmpty, Page

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Rectangle = tuple[float, float, float, float]


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PageRegion(FixtureModel):
    page: Page
    regions: list[Rectangle] = Field(min_length=1)


class FigureConfig(FixtureModel):
    figure_id: NonEmpty
    page: Page
    crop: Rectangle
    caption_region: Rectangle
    related_pages: list[Page] = Field(min_length=1)


class SmokeQuery(FixtureModel):
    query: NonEmpty
    expected_pages: list[Page] = Field(min_length=1)
    expected_terms: list[NonEmpty] = Field(min_length=1)


class PaperFixtureConfig(FixtureModel):
    paper_key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
    paper_id: NonEmpty
    title: NonEmpty
    source_url: NonEmpty
    pdf_url: NonEmpty
    pdf_sha256: SHA256
    page_regions: list[PageRegion] = Field(min_length=1)
    figures: list[FigureConfig] = Field(min_length=1)
    smoke_queries: list[SmokeQuery] = Field(min_length=2)

    @model_validator(mode="after")
    def check_manual_configuration(self):
        pages = [item.page for item in self.page_regions]
        figures = [item.figure_id for item in self.figures]
        if len(set(pages)) != len(pages) or len(set(figures)) != len(figures):
            raise ValueError("Duplicate selected page or figure ID")
        rectangles = [r for p in self.page_regions for r in p.regions]
        for figure in self.figures:
            rectangles.extend([figure.crop, figure.caption_region])
            if not set(figure.related_pages) <= set(pages):
                raise ValueError("Figure context must reference selected pages")
        for query in self.smoke_queries:
            if not set(query.expected_pages) <= set(pages):
                raise ValueError("Smoke question references unselected pages")
        for x0, y0, x1, y1 in rectangles:
            if not all(math.isfinite(v) for v in [x0, y0, x1, y1]) or not (
                0 <= x0 < x1 and 0 <= y0 < y1
            ):
                raise ValueError("Invalid manual rectangle")
        return self


class PreparedPage(PageRegion):
    path: NonEmpty
    sha256: SHA256


class PreparedFigure(FigureConfig):
    asset_path: NonEmpty
    asset_sha256: SHA256
    caption: NonEmpty
    related_text: NonEmpty


class PaperMetadata(FixtureModel):
    schema_version: Literal[1] = 1
    paper_key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
    paper_id: NonEmpty
    title: NonEmpty
    source_url: NonEmpty
    pdf_url: NonEmpty
    pdf_sha256: SHA256
    page_numbering: Literal["one-based PDF page index"] = "one-based PDF page index"
    preparation: Literal[
        "Manual page regions and fixed figure crops; not a general PDF parser."
    ] = "Manual page regions and fixed figure crops; not a general PDF parser."
    pages: list[PreparedPage] = Field(min_length=1)
    figures: list[PreparedFigure] = Field(min_length=1)
    smoke_queries: list[SmokeQuery] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_provenance(self):
        PaperFixtureConfig(
            paper_key=self.paper_key,
            paper_id=self.paper_id,
            title=self.title,
            source_url=self.source_url,
            pdf_url=self.pdf_url,
            pdf_sha256=self.pdf_sha256,
            page_regions=[
                PageRegion(page=p.page, regions=p.regions) for p in self.pages
            ],
            figures=[
                FigureConfig(
                    **f.model_dump(
                        include={
                            "figure_id",
                            "page",
                            "crop",
                            "caption_region",
                            "related_pages",
                        }
                    )
                )
                for f in self.figures
            ],
            smoke_queries=self.smoke_queries,
        )
        return self
