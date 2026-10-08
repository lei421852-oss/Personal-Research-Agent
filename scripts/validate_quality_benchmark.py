"""校验候选/Gold 来源与隔离，输出人工表单；不运行任何模型。"""

import argparse
import json
from pathlib import Path

from eval.research_quality.annotation_template import annotation_template
from eval.research_quality.dataset_loader import DEFAULT_DATASET, ROOT, load_dataset
from eval.research_quality.dataset_validator import (
    BenchmarkValidationError,
    source_index,
    validate_dataset,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotation-template", type=Path)
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.dataset)
        records, snapshot = source_index(ROOT)
        report = validate_dataset(dataset, records, snapshot)
        if args.annotation_template:
            args.annotation_template.parent.mkdir(parents=True, exist_ok=True)
            args.annotation_template.write_text(
                json.dumps(annotation_template(dataset), ensure_ascii=False, indent=2)
                + "\n"
            )
        code = 0
    except (BenchmarkValidationError, ValueError, OSError) as exc:
        report = {
            "status": "invalid",
            "issue_type": type(exc).__name__,
            "issues": getattr(exc, "issues", ["schema_or_file_validation_failure"]),
            "real_provider_calls": 0,
        }
        code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
