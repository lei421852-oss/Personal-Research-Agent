"""显式执行 F3.1-E 七题实验；先冒烟，再固定配置执行剩余题。"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from app.evidence.corpus import load_paper_corpus
from app.llm.provider import OpenAICompatibleLLMClient
from app.research.corpus_validation import corpus_snapshot, validate_index_records
from app.research.synthesis_models import ResearchReport
from app.retrieval.embedding import QwenEmbeddingModel
from app.retrieval.retriever import TextRetriever
from app.retrieval.vector_store import ChromaTextVectorStore
from app.vision.provider import OpenAICompatibleVisionClient
from eval.research_quality.coverage_comparison import (
    CoverageComparisonRequest,
    build_comparison_plan,
)
from eval.research_quality.coverage_execution import run_coverage_case
from scripts.evaluate_cross_paper import index_records
from scripts.smoke_research_synthesis import render_markdown
from scripts.verify_worker_coverage_baseline import verify

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "app/research/coverage_models.py",
    "app/research/coverage_worker.py",
    "eval/research_quality/coverage_comparison.py",
    "eval/research_quality/coverage_execution.py",
    "scripts/run_worker_coverage_comparison.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def load_rows(path):
    rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
    if len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate_case_id")
    return rows


def index_fingerprint(store):
    data = store.collection.get(include=["documents", "metadatas", "embeddings"])
    vectors = data["embeddings"]
    records = [
        {"id": i, "text": text, "metadata": meta, "embedding": vector.tolist()}
        for i, text, meta, vector in zip(
            data["ids"], data["documents"], data["metadatas"], vectors, strict=True
        )
    ]
    return canonical(sorted(records, key=lambda r: r["id"]))


def preflight(args):
    integrity = verify(ROOT / "docs/acceptance/stage_f3_1/baseline.json")
    if integrity["status"] != "PASS":
        raise ValueError("frozen_or_f2_artifact_changed")
    subprocess.run(
        ["git", "cat-file", "-e", "a38715b8eaa461daefce1f6713d673cf3828e73d^{commit}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    if not math.isfinite(args.case_timeout) or args.case_timeout <= 0:
        raise ValueError("case_timeout_must_be_positive_finite")
    for name in SOURCES[:3]:
        original = subprocess.check_output(
            ["git", "show", "a38715b8eaa461daefce1f6713d673cf3828e73d:" + name],
            cwd=ROOT,
        )
        if hashlib.sha256(original).hexdigest() != digest(ROOT / name):
            raise ValueError("f31_coverage_implementation_changed")
    requests = [
        CoverageComparisonRequest.model_validate(r) for r in load_rows(args.requests)
    ]
    prepared = json.loads(
        args.requests.with_name("comparison_manifest.json").read_text()
    )
    if len(requests) != 7 or digest(args.requests) != prepared["requests_sha256"]:
        raise ValueError("seven_request_identity_mismatch")
    baseline = load_rows(
        ROOT / "docs/acceptance/quality_end_to_end/results/executions.jsonl"
    )
    baseline_path = ROOT / "docs/acceptance/quality_end_to_end/results/executions.jsonl"
    if (
        len(baseline) != 90
        or digest(baseline_path) != prepared["source_execution_file_sha256"]
    ):
        raise ValueError("f2_execution_baseline_changed")
    by_id = {r["case_id"]: r for r in baseline}
    config = prepared["baseline_configuration"]
    current_config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())
    current_config["planner_mode"] = config["planner_mode"]
    if current_config != config:
        raise ValueError("production_configuration_differs_from_f2")
    load_dotenv(args.env_file, override=False)
    for key in (
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "VLM_API_KEY",
        "VLM_MODEL",
        "VLM_BASE_URL",
    ):
        if not os.environ.get(key):
            raise ValueError("missing_provider_configuration_" + key)
    for request in requests:
        row = by_id[request.case_id]
        if (
            build_comparison_plan(request).model_dump(mode="json")
            != row["detail"]["plan"]
        ):
            raise ValueError("original_research_plan_mismatch")
        if row["detail"]["configuration"] != config:
            raise ValueError("case_configuration_mismatch")
        if (
            row["detail"]["models"]["llm"] != os.environ["LLM_MODEL"]
            or row["detail"]["models"]["vlm"] != os.environ["VLM_MODEL"]
        ):
            raise ValueError("provider_model_differs_from_f2")
    corpus = load_paper_corpus(
        ROOT / config["papers_dir"],
        additional_papers_dir=ROOT / config["additional_papers_dir"],
    )
    snapshot = corpus_snapshot(
        ROOT / config["papers_dir"], corpus, ROOT / config["additional_papers_dir"]
    )
    if snapshot != prepared["baseline_corpus_snapshot"]:
        raise ValueError("corpus_snapshot_changed")
    # 只读打开已有集合；绝不调用 create/upsert/ingest 或自动更换索引。
    store = ChromaTextVectorStore(
        ROOT / config["index_dir"], config["collection"], create=False
    )
    binding = {
        "corpus_snapshot_id": snapshot["snapshot_id"],
        "embedding_model": config["embedding_model"],
        "distance": "cosine",
        "schema_version": 1,
    }
    if not store.collection.count() or store.collection.metadata != binding:
        raise ValueError("existing_index_binding_mismatch")
    validate_index_records(corpus, index_records(store))
    return requests, config, corpus, snapshot, store, integrity


def classify_infrastructure(detail):
    errors = [
        e for e in detail.get("provider_attempts", []) if e.get("status") == "error"
    ]
    codes = {e.get("error_type") for e in errors}
    merged = (detail.get("outcome") or {}).get("merged") or {}
    workers = merged.get("worker_results", detail.get("initial_worker_results", []))
    reviews = merged.get("review_results", detail.get("initial_review_results", []))
    report = (
        (detail.get("outcome") or {}).get("report") or detail.get("final_report") or {}
    )
    dependency_failure = any(
        w.get("error", {}).get("code") == "dependency_failure"
        for w in workers
        if w.get("error")
    )
    dependency_failure |= any(
        r.get("status") == "not_reviewable"
        and (r.get("issue") or {}).get("code") == "dependency_failure"
        for r in reviews
    )
    dependency_failure |= (report.get("issue") or {}).get("code") in {
        "dependency_failure",
        "provider_failure",
    }
    return dependency_failure and bool(
        codes
        & {
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
        }
    )


async def run_with_watchdog(awaitable, timeout):
    """保留取消快照后返回独立 watchdog 状态，不把超时当作证据不足。"""
    captured = {}

    async def preserving():
        try:
            return await awaitable
        except asyncio.CancelledError as exc:
            captured["snapshot"] = getattr(exc, "evaluation_snapshot", None)
            raise

    try:
        return await asyncio.wait_for(preserving(), timeout=timeout)
    except TimeoutError as exc:
        detail = captured.get("snapshot")
        if detail is None:
            raise RuntimeError("watchdog_failure_snapshot_unavailable") from exc
        return detail | {"case_watchdog_triggered": True}


def append(path, row):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


def git_state():
    """在启动实验时记录 Git，不占用正在执行的异步研究循环。"""
    return {
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "git_branch": subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip(),
        "initial_git_status": subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
        ),
    }


async def execute(args):
    requests, config, corpus, snapshot, store, integrity = preflight(args)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "experiment_manifest.json"
    fingerprints = {name: digest(ROOT / name) for name in SOURCES}
    original_index = index_fingerprint(store)
    fixed = {
        "requests_sha256": digest(args.requests),
        "source_f2_executions_sha256": digest(
            ROOT / "docs/acceptance/quality_end_to_end/results/executions.jsonl"
        ),
        "configuration": config,
        "llm_model": os.environ["LLM_MODEL"],
        "llm_base_url": os.environ["LLM_BASE_URL"],
        "vlm_model": os.environ["VLM_MODEL"],
        "vlm_base_url": os.environ["VLM_BASE_URL"],
        "case_watchdog_seconds": args.case_timeout,
        "index_content_sha256": original_index,
        "code_sha256": fingerprints,
    }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["fixed_configuration"] != fixed:
            raise ValueError("experiment_configuration_changed")
    else:
        manifest = {
            "experiment": "F3.1-E_known_failure_diagnostic",
            "created_at": datetime.now(UTC).isoformat(),
            "baseline_commit": "a38715b8eaa461daefce1f6713d673cf3828e73d",
            **(await asyncio.to_thread(git_state)),
            "fixed_configuration": fixed,
            "smoke_case_id": requests[0].case_id,
            "formal_configuration_frozen": False,
            "cases": [r.case_id for r in requests],
            "integrity_before": integrity,
            "intentional_treatment_difference": "Explicit CoverageAwarePaperWorker protocol, policy, coverage gate and deterministic claim assembly from F3.1.",
            "confounds_and_limits": [
                "Known failure diagnostic subset, not unknown-data generalization.",
                "Provider alias and stochastic responses may drift across dates; no underlying model weight revision is exposed.",
                "F2 did not archive endpoint or embedding cache revision; same configured model/index are verified, hidden revisions cannot be compared.",
                f"Read-only instrumentation and a {args.case_timeout}-second case watchdog are new; baseline had operation budgets but no archived case watchdog.",
                "Same AI-assisted semantic review family, no blind independent human Gold assessment.",
            ],
            "repairs": json.loads(
                (
                    args.output / "engineering_smoke_attempts/repair_record.json"
                ).read_text()
            )
            if (args.output / "engineering_smoke_attempts/repair_record.json").exists()
            else [],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
    execution_path = args.output / "treatment_executions.jsonl"
    done = (
        {r["case_id"]: r for r in load_rows(execution_path)}
        if execution_path.exists()
        else {}
    )
    if args.remaining and not manifest["formal_configuration_frozen"]:
        raise ValueError("successful_smoke_required_before_remaining_cases")
    selected = requests[1:] if args.remaining else requests[:1]
    pending = [r for r in selected if r.case_id not in done]
    if not pending:
        print(json.dumps({"event": "no_new_cases", "saved": len(done)}), flush=True)
        return
    # 使用同一缓存模型；离线模式仅阻止不必要的下载，不改变表示或排名。
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    initialized = perf_counter()
    embedding = QwenEmbeddingModel(config["embedding_model"], device=config["device"])
    retriever = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
    manifest.setdefault("initialization_records", []).append(
        {"embedding_initialization_ms": (perf_counter() - initialized) * 1000}
    )
    print(
        json.dumps(
            {
                "event": "initialized",
                "pending": len(pending),
                "index_records": store.collection.count(),
            }
        ),
        flush=True,
    )
    for request in pending:
        print(
            json.dumps({"event": "case_start", "case_id": request.case_id}), flush=True
        )

        def client_factory(role, task):
            return OpenAICompatibleLLMClient(
                api_key=os.environ["LLM_API_KEY"],
                model=os.environ["LLM_MODEL"],
                base_url=os.environ["LLM_BASE_URL"],
                timeout=config["runtime"]["operation_timeout_seconds"],
            )

        def vision_factory(task):
            return OpenAICompatibleVisionClient(
                api_key=os.environ["VLM_API_KEY"],
                model=os.environ["VLM_MODEL"],
                base_url=os.environ["VLM_BASE_URL"],
                timeout=config["runtime"]["operation_timeout_seconds"],
            )

        detail = await run_with_watchdog(
            run_coverage_case(
                request,
                corpus,
                snapshot,
                config,
                retriever=retriever,
                client_factory=client_factory,
                vision_client_factory=vision_factory,
            ),
            args.case_timeout,
        )
        report = (detail.get("outcome") or {}).get("report")
        prediction = {
            "case_id": request.case_id,
            "system": "coverage_worker_f3_1",
            "answer": render_markdown(ResearchReport.model_validate(report))
            if report
            else None,
        }
        row = {
            "case_id": request.case_id,
            "input_projection": request.model_dump(mode="json"),
            "prediction": prediction,
            "detail": detail,
            "infra_failure": classify_infrastructure(detail),
        }
        append(execution_path, row)
        append(args.output / "treatment_predictions.jsonl", prediction)
        append(
            args.output / "coverage_diagnostics.jsonl",
            {"case_id": request.case_id, "workers": detail["coverage_diagnostics"]},
        )
        done[request.case_id] = row
        smoke_ok = bool(
            report
            and not row["infra_failure"]
            and not detail.get("failure")
            and detail.get("tool_observations")
        )
        if not args.remaining:
            manifest["smoke_result"] = {
                "case_id": request.case_id,
                "engineering_chain_passed": smoke_ok,
                "semantic_quality_used_to_tune": False,
            }
            manifest["formal_configuration_frozen"] = smoke_ok
        manifest["completed_case_ids"] = list(done)
        manifest["index_after_sha256"] = index_fingerprint(store)
        manifest["integrity_after"] = verify(
            ROOT / "docs/acceptance/stage_f3_1/baseline.json"
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        if (
            manifest["index_after_sha256"] != original_index
            or manifest["integrity_after"]["status"] != "PASS"
        ):
            raise ValueError("protected_data_changed_during_experiment")
        print(
            json.dumps(
                {
                    "event": "case_saved",
                    "case_id": request.case_id,
                    "status": detail["status"],
                    "report_status": report.get("status") if report else None,
                    "infra_failure": row["infra_failure"],
                    "model_calls": (detail.get("metrics") or {}).get(
                        "total_model_calls"
                    ),
                    "tool_calls": (detail.get("metrics") or {}).get("total_tool_calls"),
                }
            ),
            flush=True,
        )
        if not args.remaining and not smoke_ok:
            print(
                json.dumps({"event": "smoke_blocked_remaining_not_started"}), flush=True
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requests",
        type=Path,
        default=ROOT / "docs/acceptance/stage_f3_1/comparison/requests.jsonl",
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/quality-f3-1-comparison"
    )
    parser.add_argument("--case-timeout", type=float, default=420)
    parser.add_argument("--remaining", action="store_true")
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
