"""汇总已测成本与召回；未经人工审阅的语义质量仍记为未知。"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from eval.metrics.task_success import apply_review
from eval.schemas import EvaluationReview, EvaluationRun


def read_runs(path):
    runs = [
        EvaluationRun.model_validate_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [r.run_id for r in runs]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate run_id in results")
    if len({r.backend for r in runs}) > 1:
        raise ValueError("Fake and real results must not be combined")
    return runs


def review_runs(runs, path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {r.run_id: r for r in runs}
    seen = set()
    for entry in data["reviews"]:
        if (
            entry.get("task_success") is None
            and entry.get("citation_correctness") is None
        ):
            continue
        fields = {
            k: entry[k]
            for k in [
                "run_id",
                "reviewer",
                "reviewed_at",
                "task_success",
                "citation_correctness",
                "notes",
            ]
            if k in entry
        }
        review = EvaluationReview.model_validate(fields)
        if review.run_id not in by_id or review.run_id in seen:
            raise ValueError("Review run_id is unknown or duplicated")
        if (
            entry.get("case_id", by_id[review.run_id].case_id)
            != by_id[review.run_id].case_id
            or entry.get("system", by_id[review.run_id].system)
            != by_id[review.run_id].system
        ):
            raise ValueError("Review case_id/system does not match its run")
        seen.add(review.run_id)
        by_id[review.run_id] = apply_review(by_id[review.run_id], review)
    return [by_id[r.run_id] for r in runs]


def nullable_mean(values):
    known = [v for v in values if v is not None]
    return mean(known) if known else None


def group_summary(runs):
    task = [r.task_success for r in runs]
    citing = [r for r in runs if r.citations]
    by_k = defaultdict(list)
    for r in runs:
        if r.text_recall_at_k is not None:
            by_k[r.text_recall_k].append(r.text_recall_at_k)
    semantic_citations = [r.citation_correctness for r in citing]
    return {
        "runs": len(runs),
        "statuses": dict(Counter(r.status for r in runs)),
        "task_success_known_count": sum(v is not None for v in task),
        "pending_task_reviews": sum(v is None for v in task),
        "task_success_rate": mean(task)
        if task and all(v is not None for v in task)
        else None,
        "citation_correctness": nullable_mean(semantic_citations)
        if citing and all(v is not None for v in semantic_citations)
        else None,
        "pending_citation_reviews": sum(v is None for v in semantic_citations),
        "citation_identity_valid_rate_among_citing_runs": mean(
            r.citation_identity_valid for r in citing
        )
        if citing
        else None,
        "citation_relevance_precision": nullable_mean(
            [r.citation_relevance_precision for r in runs]
        ),
        "text_recall_by_k": {
            str(k): {"mean": mean(v), "applicable_runs": len(v)}
            for k, v in sorted(by_k.items())
        },
        "text_evidence_recall": nullable_mean([r.text_evidence_recall for r in runs]),
        "figure_acquisition_recall_by_k": {
            str(k): {
                "mean": nullable_mean(
                    [r.figure_recall_at_k for r in runs if r.figure_recall_k == k]
                ),
                "applicable_runs": sum(
                    r.figure_recall_k == k and r.figure_recall_at_k is not None
                    for r in runs
                ),
            }
            for k in sorted({r.figure_recall_k for r in runs})
        },
        "latency_ms_mean": mean(r.latency_ms for r in runs),
        "model_calls_mean": mean(r.model_calls for r in runs),
        "vlm_calls_mean": mean(r.vlm_calls for r in runs),
        "tool_calls_mean": mean(r.tool_calls for r in runs),
        "model_calls_total": sum(r.model_calls for r in runs),
        "vlm_calls_total": sum(r.vlm_calls for r in runs),
        "tool_calls_total": sum(r.tool_calls for r in runs),
    }


def summarize(runs):
    if len({r.backend for r in runs}) > 1:
        raise ValueError("Fake and real results must not be combined")
    groups = defaultdict(list)
    categories = defaultdict(list)
    for r in runs:
        groups[r.system].append(r)
        categories[(r.system, r.category)].append(r)
    return {
        "backend": runs[0].backend if runs else None,
        "runs": len(runs),
        "systems": {k: group_summary(v) for k, v in sorted(groups.items())},
        "by_category": {
            f"{system}/{category}": group_summary(v)
            for (system, category), v in sorted(categories.items())
        },
        "notes": [
            "Completed status is not Task Success. Pending semantic judgments are null, never silently counted as success or zero.",
            "Gold-ID precision measures annotation relevance, not semantic citation correctness.",
            "Figure recall uses the first K acquired figures, not ranked image/vector retrieval.",
            "Text Recall@K is the first search at its actual K; compare grouped K values. Read-page context uses separate cumulative evidence recall.",
            "Tool latency includes VLM latency; never add inclusive tool latency and VLM latency twice.",
            "Fake results validate wiring only; do not use fake latency/quality as real experimental evidence.",
        ],
    }


def write_summary(runs, prefix):
    prefix = Path(prefix)
    summary = summarize(runs)
    Path(str(prefix) + ".json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 三系统评测汇总",
        "",
        f"Backend: {summary['backend']}；运行记录：{len(runs)}。",
        "",
        "未人工评分的 Task Success / Citation Correctness 显示为待审阅；完成回答不等于正确回答。",
        "",
        "| System | Runs | Statuses | Task Success | Pending Task Reviews | Mean ms | Mean LLM | Mean VLM | Mean Tools |",
        "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for system, g in summary["systems"].items():
        score = (
            f"{g['task_success_rate']:.3f}"
            if g["task_success_rate"] is not None
            else "待审阅"
        )
        lines.append(
            f"| {system} | {g['runs']} | {g['statuses']} | {score} | {g['pending_task_reviews']} | {g['latency_ms_mean']:.2f} | {g['model_calls_mean']:.2f} | {g['vlm_calls_mean']:.2f} | {g['tool_calls_mean']:.2f} |"
        )
    lines += ["", "## 指标协议", ""] + [f"- {n}" for n in summary["notes"]]
    lines += [
        "",
        "## 单题结果",
        "",
        "| Case | System | Status | Task Success | Text Recall@K | Figure acquired Recall@K | ms |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for r in runs:
        lines.append(
            f"| {r.case_id} | {r.system} | {r.status} | {r.task_success if r.task_success is not None else '待审阅'} | {r.text_recall_at_k} @ {r.text_recall_k} | {r.figure_recall_at_k} @ {r.figure_recall_k} | {r.latency_ms:.2f} |"
        )
    Path(str(prefix) + ".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    runs = read_runs(args.runs)
    if args.reviews:
        runs = review_runs(runs, args.reviews)
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    write_summary(runs, args.output_prefix)
    print(f"Summary saved: {args.output_prefix}.json / .md")


if __name__ == "__main__":
    main()
