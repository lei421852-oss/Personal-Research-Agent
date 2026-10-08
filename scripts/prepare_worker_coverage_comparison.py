"""从已有失败诊断准备 F3.1 同题对照输入，不调用 Provider 或检索器。"""

import argparse
import json
from pathlib import Path

from eval.research_quality.coverage_comparison import prepare_comparison

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executions",
        type=Path,
        default=ROOT / "docs/acceptance/quality_end_to_end/results/executions.jsonl",
    )
    parser.add_argument(
        "--findings",
        type=Path,
        default=ROOT / "docs/diagnostics/quality_failures_2026_10_09/findings.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/worker-coverage-comparison-f3-1",
    )
    parser.add_argument(
        "--diagnostic-executions",
        type=Path,
        help="可选：核对原七条逐字导出轨迹，并额外记录其文件指纹。",
    )
    args = parser.parse_args()
    try:
        manifest = prepare_comparison(
            args.executions,
            args.findings,
            args.output,
            diagnostic_executions=args.diagnostic_executions,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "invalid_input",
                    "issue_type": type(exc).__name__,
                    "provider_calls": 0,
                    "retrieval_calls": 0,
                }
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "case_count": manifest["case_count"],
                "provider_calls": 0,
                "retrieval_calls": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
