"""消费已保存的独立 AI 复核，生成逐题对照；不调用 Provider 或重跑研究。"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from eval.research_quality.coverage_experiment_metrics import build_comparison
from scripts.run_worker_coverage_comparison import digest, load_rows

ROOT = Path(__file__).resolve().parents[1]


def reviewed_record_hash(record):
    """独立复核采用排序键及默认 JSON 分隔符；与索引指纹格式分开。"""
    return hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def semantic_projection(record):
    return {
        "semantic_correct": record["semantic_correct"],
        "six_dimension_scores": record["scores"],
        "comparison_rubric": record["comparison_rubric"],
        "semantic_complete_requirements": sum(record["per_paper_complete"].values()),
        "assessed_requirements": len(record["per_paper_complete"]),
        "unsupported_additional_assertions": record[
            "unsupported_additional_assertion_count"
        ],
        "evidence_boundary_violations": record["support_issue_count"],
        "premature_supported": record["premature_supported_requirement_count"],
        "effective_answer": record["valid_answer_delivered"],
        "abstention_reasonable": record["abstention"]["reasonableness"],
    }


def summarize(output, review_path):
    review = json.loads(review_path.read_text())
    treatment = load_rows(output / "treatment_executions.jsonl")
    selected_ids = {r["case_id"] for r in treatment}
    sources = {
        "baseline_executions": ROOT
        / "docs/acceptance/quality_end_to_end/results/executions.jsonl",
        "baseline_assessments": ROOT
        / "docs/acceptance/quality_end_to_end/results/ai-assessments.jsonl",
        "treatment_executions": output / "treatment_executions.jsonl",
        "comparison_reference": ROOT
        / "data/benchmark/research_quality_v1_ai_reference/comparison_cases.jsonl",
    }
    for name, source in sources.items():
        if digest(source) != review["source_hashes"][name]["sha256"]:
            raise ValueError("review_source_file_hash_mismatch")
    original_assessments = {
        r["case_id"]: r for r in load_rows(sources["baseline_assessments"])
    }
    original = [
        r
        for r in load_rows(
            ROOT / "docs/acceptance/quality_end_to_end/results/executions.jsonl"
        )
        if r["case_id"] in selected_ids
    ]
    old = {r["case_id"]: r for r in original}
    new = {r["case_id"]: r for r in treatment}
    if len(treatment) != 7 or {r["case_id"] for r in review["cases"]} != selected_ids:
        raise ValueError("independent_review_set_mismatch")
    assessments = []
    for case in review["cases"]:
        identity = case["case_id"]
        if case["original_f2_assessment"] != original_assessments[identity]:
            raise ValueError("original_f2_assessment_changed")
        if (
            reviewed_record_hash(old[identity]) != case["baseline_record_sha256"]
            or reviewed_record_hash(new[identity]) != case["treatment_record_sha256"]
        ):
            raise ValueError("review_execution_hash_mismatch")
        sensitivity = case["sensitivity"]
        assessment = semantic_projection(case["treatment_semantics"]) | {
            "case_id": identity,
            "method": "ai_semantic_review",
            "assessor": review["protocol"]["assessor"],
            "notes": case["treatment_semantics"]["rationale"],
            "baseline_semantics": semantic_projection(case["baseline_semantics"]),
            "original_f2_semantic_correct": case["original_f2_assessment"][
                "semantic_correct"
            ],
            "original_f2_assessment": case["original_f2_assessment"],
            "change_class": case["change"]["classification"],
            "change_reason": case["change"]["reason"],
            "sensitivity": {
                "threshold_description": review["protocol"][
                    "core_bounded_scope_sensitivity_definition"
                ],
                "disputed_original_case": sensitivity["disputed_original_case"],
                "baseline_strict_passed": sensitivity["baseline_strict_acceptable"],
                "baseline_core_scope_passed": sensitivity[
                    "baseline_core_bounded_scope_acceptable"
                ],
                "treatment_strict_passed": sensitivity["treatment_strict_acceptable"],
                "treatment_core_scope_passed": sensitivity[
                    "treatment_core_bounded_scope_acceptable"
                ],
                "note": sensitivity["note"],
            },
            "source_review_sha256": digest(review_path),
        }
        assessments.append(assessment)
    (output / "semantic_assessments.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in assessments)
    )
    rows, summary = build_comparison(original, treatment, assessments)
    summary["independent_ai_review"] = review["summary"]
    summary["semantic_protocol"] = review["protocol"]
    summary["source_independent_review_sha256"] = digest(review_path)
    (output / "comparison_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "comparison_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    by_review = {r["case_id"]: r for r in review["cases"]}
    fields = [
        "case_id",
        "dimension",
        "question",
        "original_f2_quality",
        "treatment_quality",
        "change_class",
        "baseline_model_calls",
        "treatment_model_calls",
        "baseline_tool_calls",
        "treatment_tool_calls",
        "baseline_latency_ms",
        "treatment_latency_ms",
        "baseline_semantic_complete",
        "treatment_semantic_complete",
        "structured_complete",
        "baseline_unsupported_additional_assertions",
        "treatment_unsupported_additional_assertions",
        "worker_statuses",
        "baseline_worker_statuses",
        "reviewer_statuses",
        "baseline_reviewer_statuses",
        "report_status",
        "baseline_report_status",
        "infra_failure",
        "output_protocol_failure",
        "recovered_core_facts",
        "quality_change_reason",
    ]
    with (output / "comparison_results.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            detail = by_review[row["case_id"]]
            writer.writerow(
                {
                    "case_id": row["case_id"],
                    "dimension": detail["dimension"],
                    "question": detail["question"],
                    "original_f2_quality": row["original_f2_semantic_correct"],
                    "treatment_quality": row["treatment_semantics"]["semantic_correct"],
                    "change_class": row["change_class"],
                    "baseline_model_calls": row["baseline_cost"]["model_calls"],
                    "treatment_model_calls": row["treatment_cost"]["model_calls"],
                    "baseline_tool_calls": row["baseline_cost"]["tool_calls"],
                    "treatment_tool_calls": row["treatment_cost"]["tool_calls"],
                    "baseline_latency_ms": row["baseline_cost"]["latency_ms"],
                    "treatment_latency_ms": row["treatment_cost"]["latency_ms"],
                    "baseline_semantic_complete": json.dumps(
                        row["baseline_semantics"]["semantic_completeness"]
                    ),
                    "treatment_semantic_complete": json.dumps(
                        row["treatment_semantics"]["semantic_completeness"]
                    ),
                    "structured_complete": json.dumps(
                        row["structured_coverage"]["complete_requirement_rate"]
                    ),
                    "baseline_unsupported_additional_assertions": row[
                        "baseline_semantics"
                    ]["unsupported_additional_assertions"],
                    "treatment_unsupported_additional_assertions": row[
                        "treatment_semantics"
                    ]["unsupported_additional_assertions"],
                    "worker_statuses": json.dumps(
                        row["treatment_execution"]["worker_statuses"]
                    ),
                    "baseline_worker_statuses": json.dumps(
                        row["baseline_execution"]["worker_statuses"]
                    ),
                    "reviewer_statuses": json.dumps(
                        row["treatment_execution"]["reviewer_status_counts"]
                    ),
                    "baseline_reviewer_statuses": json.dumps(
                        row["baseline_execution"]["reviewer_status_counts"]
                    ),
                    "report_status": row["treatment_execution"]["report_status"],
                    "baseline_report_status": row["baseline_execution"][
                        "report_status"
                    ],
                    "infra_failure": row["treatment_execution"]["infra_failure"],
                    "output_protocol_failure": row["treatment_execution"][
                        "output_protocol_failure"
                    ],
                    "recovered_core_facts": json.dumps(
                        detail["change"]["recovered_missing_core_facts"],
                        ensure_ascii=False,
                    ),
                    "quality_change_reason": row["change_reason"],
                }
            )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/quality-f3-1-comparison"
    )
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    summary = summarize(
        args.output, args.review or args.output / "independent_ai_review.json"
    )
    print(
        json.dumps(
            {
                "cases": summary["case_count"],
                "quality": summary["treatment_quality"],
                "changes": summary["change_counts"],
                "cost_change": summary["cost_change"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
