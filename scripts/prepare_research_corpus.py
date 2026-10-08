"""复用固定 PDF 人工区域准备器；导出可验证的来源清单和段落位置。"""

import argparse
import json
from pathlib import Path

from app.evidence.fixtures import PaperFixtureConfig
from app.evidence.preparation import prepare_fixture
from app.research.corpus_validation import write_provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers-dir", type=Path, default=Path("data/papers"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    annotations = {}
    if args.config:
        data = json.loads(args.config.read_text())
        config = PaperFixtureConfig.model_validate(data["fixture"])
        annotations[config.paper_key] = data.get("section_titles", {})
        if not args.pdf:
            parser.error("--config requires --pdf")
        prepare_fixture(config, args.pdf, args.papers_dir / config.paper_key)
    for directory in sorted(args.papers_dir.iterdir()):
        if not directory.is_dir() or not (directory / "metadata.json").exists():
            continue
        # 无对应新配置的旧语料保留已有章节标注，幂等重建不会擦除它们。
        titles = annotations.get(directory.name)
        if titles is None and (directory / "prepared_evidence.jsonl").exists():
            titles = {}
            for line in (
                (directory / "prepared_evidence.jsonl").read_text().splitlines()
            ):
                row = json.loads(line)
                if row["evidence_type"] == "text":
                    titles.setdefault(str(row["page_number"]), {})[
                        str(row["source_span"][0]["region_index"])
                    ] = row["section_title"]
        manifest, rows = write_provenance(directory, section_titles=titles)
        print(manifest.paper_id, len(rows), manifest.source_sha256)


if __name__ == "__main__":
    main()
