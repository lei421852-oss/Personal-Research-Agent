"""兼容入口；BLIP 的人工坐标保存在 JSON 配置中。"""

import argparse
from pathlib import Path

from app.evidence.fixtures import PaperFixtureConfig
from app.evidence.preparation import prepare_fixture

ROOT = Path(__file__).resolve().parents[1]


def prepare(pdf_path: Path, output: Path):
    config = PaperFixtureConfig.model_validate_json(
        (ROOT / "data/fixture_configs/blip.json").read_text(encoding="utf-8")
    )
    return prepare_fixture(config, pdf_path, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/papers/blip")
    args = parser.parse_args()
    prepare(args.pdf, args.output)
