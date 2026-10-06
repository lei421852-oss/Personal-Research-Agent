"""顺序执行且隔离答案标注；每次完成后立即写入 JSONL。"""

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.loop import POLICY
from app.diagnostics import failure_details
from eval.loader import DATASETS, load_datasets
from eval.runners.fixed_multimodal_rag import FixedMultimodalRAGRunner
from eval.runners.multimodal_agent import MultimodalAgentRunner
from eval.runners.resources import create_resources
from eval.runners.text_rag import GENERATION_POLICY, TextRAGRunner
from eval.summarize_results import write_summary
from eval.validate_dataset import validate_datasets

OUTPUT = Path("/mnt/d/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体")
SYSTEMS = {
    "text_rag": TextRAGRunner,
    "fixed_multimodal_rag": FixedMultimodalRAGRunner,
    "multimodal_agent": MultimodalAgentRunner,
}


def review_template(runs, cases):
    by_id = {c.case_id: c for c in cases}
    return {
        "instructions": "Review against approved answer_points/must_not_claim and actual supplied observations. Fill only after inspecting each answer. Dataset approval is not run-quality approval. Do not use completed status or ID membership as semantic grading.",
        "reviews": [
            {
                "run_id": r.run_id,
                "case_id": r.case_id,
                "system": r.system,
                "question": by_id[r.case_id].question,
                "answer": r.answer,
                "status": r.status,
                "task_success_current": r.task_success,
                "task_success_source": r.task_success_source,
                "expected_behavior": by_id[r.case_id].expected_behavior,
                "actual_observations": r.trace,
                "citations": [c.model_dump(mode="json") for c in r.citations],
                "answer_points": [
                    p.model_dump(mode="json") for p in by_id[r.case_id].answer_points
                ],
                "must_not_claim": by_id[r.case_id].must_not_claim,
                "task_success": None,
                "citation_correctness": None,
                "reviewer": None,
                "reviewed_at": None,
                "notes": [],
            }
            for r in runs
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["fake", "real"], default="fake")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument(
        "--systems", nargs="+", choices=list(SYSTEMS), default=list(SYSTEMS)
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--figure-k", type=int, default=1)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.top_k <= 0 or args.figure_k <= 0:
        parser.error("Cutoffs must be positive")
    if len(args.systems) != len(set(args.systems)):
        parser.error("Systems must be unique")
    if args.env_file:
        os.environ["AGENT_ENV_FILE"] = str(args.env_file.resolve())
    resources = create_resources(args.backend)
    dev, test = load_datasets(require_approved=True)
    validation = validate_datasets(dev, test, resources.corpus)
    cases = [
        *(dev.cases if args.split in {"dev", "all"} else []),
        *(test.cases if args.split in {"test", "all"} else []),
    ]
    if args.case_id:
        if not set(args.case_id).issubset(c.case_id for c in cases):
            parser.error("case-id is outside selected split")
        cases = [c for c in cases if c.case_id in args.case_id]
    stamp = datetime.now(ZoneInfo("Asia/Shanghai"))
    prefix = f"{stamp:%Y-%m-%d}-多模态论文研究智能体-Day8-{args.backend}-{args.split}-{stamp:%H%M%S-%f}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = args.output_dir / prefix
    runs_path = Path(str(base) + "-runs.jsonl")
    manifest_path = Path(str(base) + "-manifest.json")
    manifest = {
        "schema_version": 1,
        "backend": args.backend,
        "split": args.split,
        "systems": args.systems,
        "case_ids": [c.case_id for c in cases],
        "top_k": args.top_k,
        "figure_k": args.figure_k,
        "retrieval_backend": os.environ.get(
            "AGENT_RETRIEVAL_BACKEND", "day5-controlled"
        )
        if args.backend == "real"
        else "day5-controlled",
        "LLM_model": os.environ.get("LLM_MODEL") if args.backend == "real" else "fake",
        "VLM_model": os.environ.get("VLM_MODEL") if args.backend == "real" else "fake",
        "dataset_sha256": {
            split: hashlib.sha256((DATASETS / f"{split}.json").read_bytes()).hexdigest()
            for split in ["dev", "test"]
        },
        "corpus_pdf_sha256": {
            m.paper_id: m.pdf_sha256 for m in resources.corpus.metadata
        },
        "agent_policy_sha256": hashlib.sha256(POLICY.encode()).hexdigest(),
        "text_generation_policy_sha256": hashlib.sha256(
            GENERATION_POLICY.encode()
        ).hexdigest(),
        "input_policy": "Question only. Never inject gold paper_ids, target_figures, answer_points or relevant IDs into model requests.",
        "fixed_baseline_policy": "One explicit figure resolved from question/public corpus catalog -> unchanged Day 4 workflow on figure page. Otherwise fixed Text RAG; ambiguous explicit figure is unsupported.",
        "timing_policy": "Shared corpus/retriever and provider initialization excluded; sequential calls, no caching, no automatic retries. Decision model calls and VLM calls are separate.",
        "validation": validation,
        "status": "running",
        "completed_runs": 0,
    }

    def save_manifest():
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    save_manifest()
    runs = []
    runners = {
        system: SYSTEMS[system](resources, top_k=args.top_k, figure_k=args.figure_k)
        for system in args.systems
    }
    try:
        with runs_path.open("w", encoding="utf-8") as output:
            for case in cases:
                for system, runner in runners.items():
                    print(
                        f"[{len(runs) + 1}/{len(cases) * len(runners)}] {case.case_id} / {system} starting",
                        flush=True,
                    )
                    run = runner.run(case)
                    output.write(run.model_dump_json() + "\n")
                    output.flush()
                    runs.append(run)
                    manifest["completed_runs"] = len(runs)
                    save_manifest()
                    print(
                        f"  {run.status}: tools={run.tool_calls}, LLM={run.model_calls}, VLM={run.vlm_calls}, ms={run.latency_ms:.2f}",
                        flush=True,
                    )
    except Exception as exc:  # noqa: BLE001 - 保存安全失败信息，不暴露提供方原始状态
        manifest["status"] = "interrupted"
        manifest["failure"] = failure_details(
            exc,
            phase="evaluation_runner",
            summary="Evaluation interrupted; already written runs are retained.",
        )
        save_manifest()
        raise SystemExit(json.dumps(manifest["failure"], ensure_ascii=False)) from None
    manifest["status"] = "completed"
    save_manifest()
    write_summary(runs, str(base) + "-summary")
    Path(str(base) + "-review-template.json").write_text(
        json.dumps(review_template(runs, cases), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Runs: {runs_path}\nSummary: {base}-summary.md\nRun count: {len(runs)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
