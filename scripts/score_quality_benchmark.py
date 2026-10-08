"""只读取预测与人工评分记录；无 Gold 时输出空分母，不启动 Provider。"""

import argparse
import json
from pathlib import Path

from eval.research_quality.dataset_loader import (
    DEFAULT_DATASET,
    ROOT,
    load_dataset,
    load_jsonl,
)
from eval.research_quality.dataset_validator import (
    BenchmarkValidationError,
    source_index,
    validate_dataset,
)
from eval.research_quality.schemas import HumanAssessment, QualityPrediction
from eval.research_quality.scoring import score_benchmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--assessments", type=Path)
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--reference-kind", choices=["human_gold", "ai_reviewed"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.dataset)
        records, snapshot = source_index(ROOT)
        validation = validate_dataset(dataset, records, snapshot)
        predictions = (
            load_jsonl(args.predictions, QualityPrediction) if args.predictions else []
        )
        assessments = (
            load_jsonl(args.assessments, HumanAssessment) if args.assessments else []
        )
        report = score_benchmark(
            dataset,
            predictions,
            assessments,
            split=args.split,
            reference_kind=args.reference_kind,
        ) | {"dataset_validation": validation}
        code = 0
    except (BenchmarkValidationError, ValueError, OSError) as exc:
        report = {
            "status": "invalid_input",
            "issue_type": type(exc).__name__,
            "issues": getattr(
                exc, "issues", ["dataset_prediction_or_annotation_validation_failure"]
            ),
            "real_provider_calls": 0,
        }
        code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"case_results", "metrics"}},
            ensure_ascii=False,
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
