"""按统一来源契约加载所有已准备的论文。"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.evidence.fixtures import PaperMetadata
from app.evidence.models import FigureEvidence, TextEvidence, make_evidence_id
from app.evidence.preparation import file_sha256


@dataclass(frozen=True)
class EvidenceLocation:
    paper_id: str
    page: int
    pdf_path: Path
    page_text_path: Path | None
    figure_id: str | None = None
    image_path: Path | None = None
    caption: str | None = None


@dataclass(frozen=True)
class PaperCorpus:
    text_evidence: tuple[TextEvidence, ...]
    figure_evidence: tuple[FigureEvidence, ...]
    paper_aliases: Mapping[str, str]
    locations: Mapping[str, EvidenceLocation]
    metadata: tuple[PaperMetadata, ...]

    def locate(self, evidence_id: str) -> EvidenceLocation:
        return self.locations[evidence_id]


def _local_file(paper_dir: Path, relative: str) -> Path:
    root = paper_dir.resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError("Fixture path escapes its paper directory")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _checked_file(paper_dir: Path, relative: str, digest: str) -> Path:
    path = _local_file(paper_dir, relative)
    if file_sha256(path) != digest:
        raise ValueError(f"Fixture hash mismatch: {relative}")
    return path


def _load_paper(paper_dir: Path):
    metadata = PaperMetadata.model_validate_json(
        (paper_dir / "metadata.json").read_text(encoding="utf-8")
    )
    pdf = _checked_file(paper_dir, "paper.pdf", metadata.pdf_sha256)
    texts, figures, locations, pages = {}, [], {}, {}
    for entry in metadata.pages:
        path = _checked_file(paper_dir, entry.path, entry.sha256)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("Empty prepared page")
        pages[entry.page] = (path, text)
        for paragraph in text.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph:
                item = TextEvidence(
                    evidence_id=make_evidence_id(
                        metadata.paper_id, entry.page, "text", paragraph
                    ),
                    paper_id=metadata.paper_id,
                    page=entry.page,
                    text=paragraph,
                )
                texts[item.evidence_id] = item
                locations[item.evidence_id] = EvidenceLocation(
                    metadata.paper_id, entry.page, pdf, path
                )
    for entry in metadata.figures:
        path = _checked_file(paper_dir, entry.asset_path, entry.asset_sha256)
        related = "\n\n".join(pages[page][1] for page in entry.related_pages)
        if related != entry.related_text:
            raise ValueError("Figure related text differs from its source pages")
        item = FigureEvidence(
            evidence_id=make_evidence_id(
                metadata.paper_id, entry.page, "figure", entry.figure_id
            ),
            paper_id=metadata.paper_id,
            page=entry.page,
            text=entry.related_text,
            figure_id=entry.figure_id,
            caption=entry.caption,
            asset_path=str(path),
        )
        figures.append(item)
        locations[item.evidence_id] = EvidenceLocation(
            metadata.paper_id,
            entry.page,
            pdf,
            pages[entry.page][0] if entry.page in pages else None,
            entry.figure_id,
            path,
            entry.caption,
        )
    if (paper_dir / "source_manifest.json").exists() or (
        paper_dir / "prepared_evidence.jsonl"
    ).exists():
        # 新语料在进入现有 Repository/索引前验证完整来源，失败时不降级加载。
        from app.research.corpus_validation import validate_prepared_paper

        validate_prepared_paper(paper_dir, metadata, list(texts.values()), figures)
    return metadata, list(texts.values()), figures, locations


def load_text_corpus(paper_dir: Path) -> list[TextEvidence]:
    """兼容 Day 1 的单论文入口，仍使用同一套加载器。"""
    return _load_paper(paper_dir)[1]


def load_paper_corpus(
    papers_dir: Path, *, additional_papers_dir: Path | None = None
) -> PaperCorpus:
    texts, figures, locations, aliases, metadata = [], [], {}, {}, []
    directories = list(papers_dir.iterdir())
    if additional_papers_dir is not None:
        directories.extend(additional_papers_dir.iterdir())
    for directory in sorted(directories):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if (
            additional_papers_dir is not None
            and directory.parent.resolve() == additional_papers_dir.resolve()
            and not all(
                (directory / name).is_file()
                for name in ("source_manifest.json", "prepared_evidence.jsonl")
            )
        ):
            raise ValueError(
                "Additional research corpus requires complete source provenance"
            )
        meta, paper_text, paper_figures, paper_locations = _load_paper(directory)
        if meta.paper_key != directory.name:
            raise ValueError("Paper key differs from directory name")
        for name in dict.fromkeys([meta.paper_key, meta.paper_id]):
            if name in aliases:
                raise ValueError("Duplicate paper identity or alias")
            aliases[name] = meta.paper_id
        if locations.keys() & paper_locations.keys():
            raise ValueError("Duplicate evidence identity across papers")
        metadata.append(meta)
        texts.extend(paper_text)
        figures.extend(paper_figures)
        locations.update(paper_locations)
    if not metadata:
        raise ValueError("No prepared papers")
    return PaperCorpus(
        tuple(texts),
        tuple(figures),
        MappingProxyType(aliases),
        MappingProxyType(locations),
        tuple(metadata),
    )
