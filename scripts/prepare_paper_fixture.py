"""用人工 JSON 配置准备固定版本 PDF，不下载文件或加载模型。"""

import argparse
from pathlib import Path

from app.evidence.fixtures import PaperFixtureConfig
from app.evidence.preparation import prepare_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = PaperFixtureConfig.model_validate_json(
        args.config.read_text(encoding="utf-8")
    )
    metadata = prepare_fixture(config, args.pdf, args.output)
    print(
        metadata.paper_id,
        "pages:",
        len(metadata.pages),
        "figures:",
        len(metadata.figures),
    )


if __name__ == "__main__":
    main()
