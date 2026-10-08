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
from eval.research_quality.schemas import (
    AIAssessment,
    HumanAssessment,
    QualityPrediction,
)
from eval.research_quality.scoring import ScoringInputError, score_benchmark


class MissingInputFiles(FileNotFoundError):
    def __init__(self, details):
        self.details = details
        self.issues = ["missing_" + item["input_name"] + "_file" for item in details]
        super().__init__("Required scoring input files do not exist")


def check_input_files(predictions, assessments, ai_assessments=None):
    """先检查用户显式指定的结果文件，避免读完整语料后只报笼统错误。"""
    details = []
    for name, path, hint in (
        (
            "predictions",
            predictions,
            "这是系统实际作答结果。先运行评测生成该文件；仅检查入口时去掉 --predictions。",
        ),
        (
            "assessments",
            assessments,
            "这是对实际模型答案的人工语义评分，不是参考样本审核记录。尚未生成时去掉 --assessments。",
        ),
        (
            "ai_assessments",
            ai_assessments,
            "这是明确标为AI来源的实际答案语义核对；只用于AI参考集，不能代替人工评分记录。",
        ),
    ):
        if path is not None and not path.is_file():
            details.append(
                {
                    "input_name": name,
                    "argument": "--" + name.replace("_", "-"),
                    "file": str(path),
                    "reason": "file_not_found"
                    if not path.exists()
                    else "not_a_regular_file",
                    "hint": hint,
                }
            )
    if details:
        raise MissingInputFiles(details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--assessments", type=Path)
    parser.add_argument("--ai-assessments", type=Path)
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--reference-kind", choices=["human_gold", "ai_reviewed"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage = "input_files"
    try:
        check_input_files(args.predictions, args.assessments, args.ai_assessments)
        stage = "dataset_load"
        dataset = load_dataset(args.dataset)
        stage = "source_validation"
        records, snapshot = source_index(ROOT)
        validation = validate_dataset(dataset, records, snapshot)
        stage = "predictions_load"
        predictions = (
            load_jsonl(args.predictions, QualityPrediction) if args.predictions else []
        )
        stage = "assessments_load"
        assessments = (
            load_jsonl(args.assessments, HumanAssessment) if args.assessments else []
        )
        stage = "ai_assessments_load"
        ai_assessments = (
            load_jsonl(args.ai_assessments, AIAssessment) if args.ai_assessments else []
        )
        stage = "scoring"
        report = score_benchmark(
            dataset,
            predictions,
            assessments,
            split=args.split,
            reference_kind=args.reference_kind,
            ai_assessments=ai_assessments,
        ) | {"dataset_validation": validation}
        code = 0
    except (BenchmarkValidationError, ValueError, OSError) as exc:
        report = {
            "status": "invalid_input",
            "issue_type": type(exc).__name__,
            "input_stage": stage,
            "issues": getattr(
                exc, "issues", ["dataset_prediction_or_annotation_validation_failure"]
            ),
            "real_provider_calls": 0,
        }
        if isinstance(exc, MissingInputFiles):
            report["input_errors"] = exc.details
        elif isinstance(exc, FileNotFoundError):
            report["issues"] = ["file_not_found"]
            report["file_name"] = Path(exc.filename).name if exc.filename else None
        elif isinstance(exc, ScoringInputError):
            # 评分异常只包含程序定义的错误码，不输出输入内容或异常堆栈。
            report["issues"] = [str(exc)]
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
