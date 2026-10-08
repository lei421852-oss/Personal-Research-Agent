"""准备阶段的来源校验；不读取 Profile，也不改变检索排序。"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from app.evidence.fixtures import SHA256, FixtureModel, PaperMetadata, Rectangle
from app.evidence.models import NonEmpty, Page, make_evidence_id
from app.evidence.preparation import file_sha256


class SourceManifest(FixtureModel):
    paper_id: NonEmpty
    title: NonEmpty
    source_url: NonEmpty
    source_version: NonEmpty
    source_sha256: SHA256
    extraction_method: Literal["manual_pdf_regions_v1"] = "manual_pdf_regions_v1"
    extraction_timestamp: datetime
    evidence_schema_version: Literal[1] = 1
    extraction_config_sha256: SHA256
    page_count: Page
    page_mapping: dict[str, str | None]
    limitations: list[NonEmpty]

    @model_validator(mode="after")
    def mapping(self):
        if self.extraction_timestamp.tzinfo is None:
            raise ValueError("Extraction timestamp requires a timezone")
        if not self.source_url.startswith(
            "https://arxiv.org/pdf/"
        ) or not self.source_url.endswith(self.source_version):
            raise ValueError("Source URL must identify the pinned PDF version")
        if any(
            not k.isdigit() or not 1 <= int(k) <= self.page_count
            for k in self.page_mapping
        ):
            raise ValueError("Page mapping is outside the PDF")
        return self


class SourceSpan(FixtureModel):
    region: Rectangle
    block_bbox: Rectangle
    region_index: StrictInt = Field(ge=0)
    block_index: StrictInt = Field(ge=0)


class PreparedEvidence(FixtureModel):
    evidence_id: NonEmpty
    paper_id: NonEmpty
    evidence_type: Literal["text", "figure"]
    page_number: Page
    section_title: NonEmpty
    content: NonEmpty
    source_span: list[SourceSpan] = Field(min_length=1)
    source_document_hash: SHA256
    figure_number: NonEmpty | None = None
    caption: NonEmpty | None = None
    associated_text_evidence_ids: list[NonEmpty] = Field(default_factory=list)
    pixels_available: bool = False

    @model_validator(mode="after")
    def modality(self):
        if self.evidence_type == "figure":
            if not self.figure_number or not self.caption or not self.pixels_available:
                raise ValueError(
                    "Figure evidence requires a verified pixel asset and caption"
                )
        elif self.figure_number or self.caption or self.pixels_available:
            raise ValueError("Text/caption evidence cannot claim pixel analysis")
        return self


def canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def extraction_hash(metadata: PaperMetadata) -> str:
    return canonical_hash(metadata.model_dump(mode="json", exclude={"smoke_queries"}))


def pdf_blocks(page, regions):
    """区域顺序由人工配置决定；区域内按纵坐标排序，拒绝覆盖两栏的正文。"""
    import pymupdf

    result = []
    seen = set()
    for ri, region in enumerate(regions):
        blocks = [
            b
            for b in page.get_text("blocks", clip=pymupdf.Rect(region))
            if b[6] == 0 and b[4].strip()
        ]
        ordered = sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))
        if blocks != ordered:
            raise ValueError("reading_order_anomaly: refine manual paragraph regions")
        for bi, block in enumerate(blocks):
            key = tuple(block[:4])
            if key in seen:
                raise ValueError("overlapping_regions: a paragraph was extracted twice")
            seen.add(key)
            result.append(
                (
                    " ".join(block[4].split()),
                    SourceSpan(
                        region=region, block_bbox=key, region_index=ri, block_index=bi
                    ),
                )
            )
    return result


def build_prepared(paper_dir: Path, metadata: PaperMetadata, *, section_titles=None):
    """仅从已校验 PDF 重建段落位置，绝不从元数据简介生成正文。"""
    import pymupdf

    rows = {}
    section_titles = section_titles or {}
    with pymupdf.open(paper_dir / "paper.pdf") as doc:
        for page in metadata.pages:
            blocks = pdf_blocks(doc[page.page - 1], page.regions)
            expected = "\n\n".join(t for t, _ in blocks)
            actual = (paper_dir / page.path).read_text().strip()
            if actual != expected:
                raise ValueError("prepared_text_differs_from_pdf")
            for text, span in blocks:
                identity = make_evidence_id(metadata.paper_id, page.page, "text", text)
                title = section_titles.get(str(page.page), {}).get(
                    str(span.region_index), "Prepared PDF context"
                )
                if identity in rows:
                    raise ValueError("duplicate_source_paragraph")
                rows[identity] = PreparedEvidence(
                    evidence_id=identity,
                    paper_id=metadata.paper_id,
                    evidence_type="text",
                    page_number=page.page,
                    section_title=title,
                    content=text,
                    source_span=[span],
                    source_document_hash=metadata.pdf_sha256,
                )
        for figure in metadata.figures:
            page = doc[figure.page - 1]
            caption = " ".join(
                page.get_text("text", clip=pymupdf.Rect(figure.caption_region)).split()
            )
            if caption != figure.caption or not caption.lower().startswith(
                figure.figure_id.lower().replace("figure-", "figure ")
            ):
                raise ValueError("figure_caption_mismatch")
            # 像素文件必须确实来自当前 PDF 的固定裁剪区域，而非另一个来源的图片。
            pixels = page.get_pixmap(
                matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(figure.crop)
            ).tobytes("png")
            if hashlib.sha256(pixels).hexdigest() != figure.asset_sha256:
                raise ValueError("figure_pixels_differ_from_pdf")
            identity = make_evidence_id(
                metadata.paper_id, figure.page, "figure", figure.figure_id
            )
            rows[identity] = PreparedEvidence(
                evidence_id=identity,
                paper_id=metadata.paper_id,
                evidence_type="figure",
                page_number=figure.page,
                section_title="Figure caption and linked context",
                content=figure.related_text,
                source_span=[
                    SourceSpan(
                        region=figure.crop,
                        block_bbox=figure.crop,
                        region_index=0,
                        block_index=0,
                    ),
                    SourceSpan(
                        region=figure.caption_region,
                        block_bbox=figure.caption_region,
                        region_index=1,
                        block_index=0,
                    ),
                ],
                source_document_hash=metadata.pdf_sha256,
                figure_number=figure.figure_id,
                caption=caption,
                associated_text_evidence_ids=[
                    r.evidence_id
                    for r in rows.values()
                    if r.evidence_type == "text"
                    and r.page_number in figure.related_pages
                ],
                pixels_available=True,
            )
    return list(rows.values())


def write_provenance(paper_dir: Path, *, section_titles=None, limitations=None):
    metadata = PaperMetadata.model_validate_json(
        (paper_dir / "metadata.json").read_text()
    )
    rows = build_prepared(paper_dir, metadata, section_titles=section_titles)
    import pymupdf

    with pymupdf.open(paper_dir / "paper.pdf") as doc:
        mapping = {}
        for p in metadata.pages:
            # 印刷页码只识别页底独立数字，未识别时保留 null，禁止猜测。
            bottom = [
                b[4].strip()
                for b in doc[p.page - 1].get_text("blocks")
                if b[6] == 0
                and b[1] > doc[p.page - 1].rect.height * 0.9
                and b[4].strip().isdigit()
            ]
            mapping[str(p.page)] = bottom[0] if len(bottom) == 1 else None
        manifest = SourceManifest(
            paper_id=metadata.paper_id,
            title=metadata.title,
            source_url=metadata.pdf_url,
            source_version=metadata.paper_id.rsplit("-", 1)[-1],
            source_sha256=metadata.pdf_sha256,
            extraction_timestamp=datetime.now(UTC),
            extraction_config_sha256=extraction_hash(metadata),
            page_count=len(doc),
            page_mapping=mapping,
            limitations=limitations
            or [
                "Only manually selected PDF paragraphs and one figure are prepared; no general PDF parser.",
                "Equations, tables and unselected page continuations are not reconstructed.",
                "Caption text is not pixel analysis; reviewers receive captions/context, not pixels.",
            ],
        )
    previous = paper_dir / "source_manifest.json"
    if previous.exists():
        old = SourceManifest.model_validate_json(previous.read_text())
        if (
            old.source_sha256 == manifest.source_sha256
            and old.extraction_config_sha256 == manifest.extraction_config_sha256
        ):
            manifest = manifest.model_copy(
                update={
                    "extraction_timestamp": old.extraction_timestamp,
                    "limitations": old.limitations
                    if limitations is None
                    else manifest.limitations,
                }
            )
    previous.write_text(manifest.model_dump_json(indent=2) + "\n")
    (paper_dir / "prepared_evidence.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in rows)
    )
    return manifest, rows


_VALIDATED = {}


def validate_prepared_paper(paper_dir: Path, metadata: PaperMetadata, texts, figures):
    # 同一进程反复读取不可变 fixture 时复用已成功的校验；任何源文件修改都会失效。
    files = [
        paper_dir / p
        for p in [
            "paper.pdf",
            "metadata.json",
            "source_manifest.json",
            "prepared_evidence.jsonl",
            *[p.path for p in metadata.pages],
            *[f.asset_path for f in metadata.figures],
        ]
    ]
    stamps = tuple(
        (str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
        for p in files
    )
    key = (
        stamps,
        canonical_hash([e.model_dump(mode="json") for e in [*texts, *figures]]),
        extraction_hash(metadata),
    )
    if key not in _VALIDATED:
        rows = _validate_prepared_paper(paper_dir, metadata, texts, figures)
        if len(_VALIDATED) >= 64:
            _VALIDATED.clear()
        _VALIDATED[key] = tuple(rows)
    return list(_VALIDATED[key])


def _validate_prepared_paper(paper_dir: Path, metadata: PaperMetadata, texts, figures):
    manifest = SourceManifest.model_validate_json(
        (paper_dir / "source_manifest.json").read_text()
    )
    if (
        manifest.paper_id,
        manifest.title,
        manifest.source_url,
        manifest.source_version,
        manifest.source_sha256,
        manifest.extraction_config_sha256,
    ) != (
        metadata.paper_id,
        metadata.title,
        metadata.pdf_url,
        metadata.paper_id.rsplit("-", 1)[-1],
        file_sha256(paper_dir / "paper.pdf"),
        extraction_hash(metadata),
    ):
        raise ValueError("source_manifest_mismatch")
    import pymupdf

    with pymupdf.open(paper_dir / "paper.pdf") as doc:
        if manifest.page_count != len(doc) or set(manifest.page_mapping) != {
            str(p.page) for p in metadata.pages
        }:
            raise ValueError("page_mapping_mismatch")
        for page, label in manifest.page_mapping.items():
            if label is not None and label not in [
                b[4].strip()
                for b in doc[int(page) - 1].get_text("blocks")
                if b[6] == 0 and b[1] > doc[int(page) - 1].rect.height * 0.9
            ]:
                raise ValueError("printed_page_label_mismatch")
    rows = [
        PreparedEvidence.model_validate_json(line)
        for line in (paper_dir / "prepared_evidence.jsonl").read_text().splitlines()
        if line.strip()
    ]
    identities = [r.evidence_id for r in rows]
    expected = {r.evidence_id: r for r in build_prepared(paper_dir, metadata)}
    if len(identities) != len(set(identities)) or set(identities) != set(expected):
        raise ValueError("prepared_evidence_identity_mismatch")
    for row in rows:
        reference = expected[row.evidence_id]
        if row.model_dump(exclude={"section_title"}) != reference.model_dump(
            exclude={"section_title"}
        ):
            raise ValueError("prepared_evidence_provenance_mismatch")
    domain = {e.evidence_id: e for e in [*texts, *figures]}
    if set(domain) != set(expected) or any(
        (e.paper_id, e.page, e.text, e.modality)
        != (
            expected[i].paper_id,
            expected[i].page_number,
            expected[i].content,
            expected[i].evidence_type,
        )
        for i, e in domain.items()
    ):
        raise ValueError("corpus_projection_mismatch")
    return rows


def validate_index_records(corpus, records):
    """必须是同一快照的完整索引，拒绝额外、缺失、重复或改写的记录。"""
    expected = {e.evidence_id: e for e in corpus.text_evidence}
    items = list(records)
    actual = {e.evidence_id: e for e in items}
    if len(actual) != len(items) or actual != expected:
        raise ValueError("index_provenance_mismatch")


def corpus_snapshot(
    papers_dir: Path, corpus, additional_papers_dir: Path | None = None
):
    entries = []
    for meta in corpus.metadata:
        directory = papers_dir / meta.paper_key
        if not directory.exists() and additional_papers_dir is not None:
            directory = additional_papers_dir / meta.paper_key
        if not (directory / "source_manifest.json").is_file():
            raise ValueError("missing_source_manifest")
        entries.append(
            {
                "paper_id": meta.paper_id,
                "source_sha256": meta.pdf_sha256,
                "metadata_sha256": file_sha256(directory / "metadata.json"),
                "prepared_sha256": file_sha256(directory / "prepared_evidence.jsonl"),
            }
        )
    return {
        "snapshot_id": canonical_hash(entries),
        "papers": entries,
        "text_count": len(corpus.text_evidence),
        "figure_count": len(corpus.figure_evidence),
        "corpus_validity_rate": 1.0,
        "evidence_provenance_validity_rate": 1.0,
    }
