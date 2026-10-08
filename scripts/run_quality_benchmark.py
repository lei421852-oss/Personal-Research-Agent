"""生成真实质量预测；逐题保存，已执行题不重复调用，缺失结果不编造。"""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from app.diagnostics import failure_details
from app.evidence.corpus import load_paper_corpus
from app.llm.provider import OpenAICompatibleLLMClient
from app.research.corpus_validation import corpus_snapshot
from eval.research_quality.dataset_loader import ROOT, approved_references, load_dataset
from eval.research_quality.dataset_validator import source_index, validate_dataset
from eval.research_quality.execution import QualityExecutionRunner
from eval.research_quality.schemas import QualityPrediction, record_sha256
from scripts.evaluate_cross_paper import real_retriever
from scripts.finalize_quality_references import OUTPUT_DATASET


def selected_cases(dataset, split, pilot):
    cases = approved_references(dataset, split=split)
    if pilot:
        return [
            next(c for c in cases if c.task_type == kind)
            for kind in ("claim", "reviewer", "comparison", "abstention")
        ]
    return cases


async def execute(args, dataset, cases):
    load_dotenv(args.env_file, override=False)
    if any(
        not os.environ.get(k)
        for k in (
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "VLM_API_KEY",
            "VLM_MODEL",
            "VLM_BASE_URL",
        )
    ):
        raise ValueError("provider_configuration_incomplete")
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())
    config["planner_mode"] = "fixed_benchmark_paper_dimension_plan_no_gold"
    corpus = load_paper_corpus(
        ROOT / config["papers_dir"],
        additional_papers_dir=ROOT / config["additional_papers_dir"],
    )
    snapshot = corpus_snapshot(
        ROOT / config["papers_dir"], corpus, ROOT / config["additional_papers_dir"]
    )
    if snapshot["snapshot_id"] != dataset.manifest.corpus_snapshot_id:
        raise ValueError("corpus_snapshot_mismatch")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_sha = hashlib.sha256(
        (args.dataset / "manifest.json").read_bytes()
    ).hexdigest()
    manifest_path = args.output_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest["dataset_manifest_sha256"] != dataset_sha
            or manifest["llm_model"] != os.environ["LLM_MODEL"]
        ):
            raise ValueError("existing_run_configuration_changed")
    else:
        manifest = {
            "run_id": datetime.now(UTC).strftime("quality-%Y%m%dT%H%M%SZ"),
            "dataset_manifest_sha256": dataset_sha,
            "llm_model": os.environ["LLM_MODEL"],
            "embedding_model": config["embedding_model"],
            "corpus_snapshot_id": snapshot["snapshot_id"],
            "reference_kind": dataset.manifest.reference_kind,
            "execution_configuration": config,
            "created_at": datetime.now(UTC).isoformat(),
            "grading": "not_yet_graded",
            "planner_tested": False,
            "hidden_reasoning_recorded": False,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
    done = {}
    execution_path = args.output_dir / "executions.jsonl"
    if execution_path.exists():
        for line in execution_path.read_text().splitlines():
            row = json.loads(line)
            if row["case_id"] in done:
                raise ValueError("duplicate_existing_execution")
            done[row["case_id"]] = row
    for case in cases:
        if case.case_id in done and done[case.case_id]["case_sha256"] != record_sha256(
            case
        ):
            raise ValueError("existing_prediction_case_changed")
    todo = [c for c in cases if c.case_id not in done]
    # 已有逐题记录可以重建结果文件，不需要再次付费调用Provider。
    (args.output_dir / "predictions.jsonl").write_text(
        "".join(
            json.dumps(r["prediction"], ensure_ascii=False) + "\n"
            for r in done.values()
        )
    )
    print(
        json.dumps(
            {
                "event": "initialize",
                "selected": len(cases),
                "already_saved": len(cases) - len(todo),
                "pending": len(todo),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    retriever = (
        real_retriever(config, corpus, snapshot)
        if any(
            c.task_type != "reviewer"
            and not (
                c.abstention
                and c.abstention.assessment_scope == "provided_caption_only"
            )
            for c in todo
        )
        else None
    )

    def client():
        return OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=config["runtime"]["operation_timeout_seconds"],
        )

    runner = QualityExecutionRunner(corpus, snapshot, config, retriever, client)
    for index, case in enumerate(todo, 1):
        print(
            json.dumps(
                {
                    "event": "case_start",
                    "index": index,
                    "pending_total": len(todo),
                    "case_id": case.case_id,
                    "task_type": case.task_type,
                    "split": case.split,
                }
            ),
            flush=True,
        )
        started = perf_counter()
        try:
            result = await runner.run(case, manifest["run_id"])
        except Exception as exc:  # noqa: BLE001 - 保存逐题失败，不编造预测结果
            result = {
                "case_id": case.case_id,
                "task_type": case.task_type,
                "split": case.split,
                "case_sha256": record_sha256(case),
                "prediction": QualityPrediction(
                    case_id=case.case_id,
                    prediction_id=manifest["run_id"] + ":" + case.case_id,
                    system="execution_failed",
                ).model_dump(mode="json"),
                "real_provider_used": None,
                "provider_attempts": None,
                "latency_ms": (perf_counter() - started) * 1000,
                "detail": {
                    "status": "execution_failed",
                    "failure": failure_details(
                        exc,
                        phase="quality_execution",
                        summary="Case execution failed; no answer fabricated.",
                    ),
                },
                "counting_limitations": "Exception before adapter returns can leave provider attempt count unavailable; not reported as zero.",
            }
        with execution_path.open("a") as file:
            file.write(json.dumps(result, ensure_ascii=False) + "\n")
            file.flush()
        done[case.case_id] = result
        (args.output_dir / "predictions.jsonl").write_text(
            "".join(
                json.dumps(r["prediction"], ensure_ascii=False) + "\n"
                for r in done.values()
            )
        )
        print(
            json.dumps(
                {
                    "event": "case_done",
                    "case_id": case.case_id,
                    "status": result["detail"].get("status"),
                    "provider_attempts": result.get("provider_attempts"),
                    "latency_ms": round(result["latency_ms"]),
                    "saved_cases": len(done),
                }
            ),
            flush=True,
        )
    print(
        json.dumps(
            {
                "event": "batch_complete",
                "saved_total": len(done),
                "selected": len(cases),
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=OUTPUT_DATASET)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    records, snapshot = source_index(ROOT)
    validation = validate_dataset(dataset, records, snapshot)
    cases = selected_cases(dataset, args.split, args.pilot)
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "validated_not_executed",
                    "selected_case_ids": [c.case_id for c in cases],
                    "validation": validation,
                    "provider_api_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return
    asyncio.run(execute(args, dataset, cases))


if __name__ == "__main__":
    main()
