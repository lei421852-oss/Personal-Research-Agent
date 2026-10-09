"""G2.1只运行一次真实验证；G2材料只读，默认仅预检。"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from time import perf_counter

from app.llm.provider import OpenAICompatibleLLMClient
from app.papers.catalog import load_paper_catalog
from app.research.planner import ResearchPlanner
from app.research.protocol_runtime import run_protocol_case
from app.tools.redaction import public_value
from app.vision.provider import OpenAICompatibleVisionClient
from scripts.run_research_demo import (
    ROOT,
    RecordingRetriever,
    index_fingerprint,
    preflight,
    save,
    write_display,
)


async def bounded_research(preparation, corpus, snapshot, config, retriever, timeout):
    cancelled = {}
    settings = {
        kind: {
            field: os.environ[f"{kind}_{field}"]
            for field in ("API_KEY", "MODEL", "BASE_URL")
        }
        for kind in ("LLM", "VLM")
    }

    def client(role):
        return OpenAICompatibleLLMClient(
            api_key=settings["LLM"]["API_KEY"],
            model=settings["LLM"]["MODEL"],
            base_url=settings["LLM"]["BASE_URL"],
            timeout=config["runtime"]["operation_timeout_seconds"],
        )

    def vision():
        return OpenAICompatibleVisionClient(
            api_key=settings["VLM"]["API_KEY"],
            model=settings["VLM"]["MODEL"],
            base_url=settings["VLM"]["BASE_URL"],
            timeout=config["runtime"]["operation_timeout_seconds"],
        )

    async def tracked():
        try:
            return await run_protocol_case(
                preparation.plan, corpus, snapshot, config, retriever, client, vision
            )
        except asyncio.CancelledError as exc:
            cancelled.update(getattr(exc, "evaluation_snapshot", {}))
            raise

    try:
        return await asyncio.wait_for(tracked(), timeout)
    except TimeoutError:
        return cancelled | {"status": "timed_out", "issue_type": "TimeoutError"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "docs/demos/blip2-vs-flamingo/execution-record.json",
    )
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/cross_paper_eval.json"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text())
    args.question, args.paper_ids = baseline["question"], baseline["paper_ids"]
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    if (output / "execution-started.json").exists() or (
        output / "execution.json"
    ).exists():
        parser.error("Existing validation cannot be overwritten; use a new directory")
    started = perf_counter()
    try:
        ready, corpus, snapshot, config, store, retriever = preflight(args)
        previous = json.loads(
            (ROOT / "docs/demos/blip2-vs-flamingo/execution-summary.json").read_text()
        )
        original = previous["configuration"]
        if (
            ready["models"] != original["models"]
            or ready["configuration"] != original["configuration"]
            or ready["index_fingerprint"] != original["index_fingerprint"]
            or args.timeout != original["case_timeout_seconds"]
            or args.paper_ids != original["paper_ids"]
            or args.question != original["question"]
        ):
            raise ValueError("g2_environment_or_request_changed")
    except Exception as exc:  # noqa: BLE001 - 不更换模型、不补建索引
        save(
            output / "preflight.json",
            {
                "status": "blocked",
                "issue_type": type(exc).__name__,
                "issue": public_value(str(exc)),
                "provider_calls": 0,
            },
        )
        print("blocked: see preflight.json", flush=True)
        return 2
    save(output / "preflight.json", ready)
    if not args.execute:
        print(
            "ready: same G2 request, providers, budgets and existing index; no Provider calls",
            flush=True,
        )
        return 0
    with (output / "execution-started.json").open("x") as stream:
        json.dump(
            {
                "formal_attempt": 1,
                "baseline_sha256": hashlib.sha256(
                    args.baseline.read_bytes()
                ).hexdigest(),
            },
            stream,
        )
    files = [
        "app/research/worker_protocol.py",
        "app/research/protocol_runtime.py",
        "scripts/run_worker_protocol_demo.py",
        "app/research/worker.py",
        "app/research/reviewer.py",
        "app/research/recovery_orchestrator.py",
        "app/research/synthesis_agent.py",
    ]
    save(
        output / "run_manifest.json",
        {
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_hashes": {
                f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files
            },
            "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
            "question": args.question,
            "paper_ids": args.paper_ids,
            "formal_attempt": 1,
        },
    )
    client = None
    recording = RecordingRetriever(retriever)
    execution = {
        "question": args.question,
        "paper_ids": args.paper_ids,
        "preflight": ready,
        "protocol_adapter_enabled": True,
        "coverage_worker_enabled": False,
    }
    research_started = perf_counter()
    try:
        client = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=config["runtime"]["operation_timeout_seconds"],
        )
        preparation = ResearchPlanner(client, load_paper_catalog()).prepare(
            args.question, args.paper_ids
        )
        execution["preparation"] = preparation.model_dump(mode="json")
        print(
            f"Real Planner: {len(preparation.plan.dimensions)} dimensions; default Worker + protocol adapter",
            flush=True,
        )
        execution["research"] = asyncio.run(
            bounded_research(
                preparation, corpus, snapshot, config, recording, args.timeout
            )
        )
    except Exception as exc:  # noqa: BLE001 - 唯一真实执行的失败也保存
        execution["failure"] = {
            "issue_type": type(exc).__name__,
            "issue": public_value(str(exc)),
        }
    finally:
        calls = client.calls if client else []
        execution["planner_events"] = public_value(calls)
        execution["planner_model_calls"] = len(calls)
        execution["research_latency_ms"] = (perf_counter() - research_started) * 1000
        execution["total_latency_ms"] = (perf_counter() - started) * 1000
        if client:
            client.close()
        execution["index_fingerprint_after"] = index_fingerprint(store)
        execution["index_unchanged"] = (
            execution["index_fingerprint_after"] == ready["index_fingerprint"]
        )
        checks = write_display(output, execution, recording.records, corpus)
        raw = execution.get("research", {})
        summary = {
            "status": raw.get("status", "execution_failed"),
            **checks,
            "planner_model_calls": len(calls),
            "llm_provider_attempts_observed": len(calls)
            + raw.get("llm_provider_attempts_observed", 0),
            "vlm_client_calls_observed": raw.get("vlm_client_calls_observed", 0),
            "research_metrics": raw.get("metrics"),
            "retrieval_attempts": len(recording.records),
            "research_latency_ms": execution["research_latency_ms"],
            "total_latency_ms": execution["total_latency_ms"],
            "index_unchanged": execution["index_unchanged"],
            "configuration": ready,
            "protocol_adapter_enabled": True,
            "worker_class": "PaperResearchWorker",
            "coverage_worker_enabled": False,
            "independent_human_review": "not_performed",
        }
        save(output / "execution-summary.json", summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in [
                    "status",
                    "report_status",
                    "llm_provider_attempts_observed",
                    "index_unchanged",
                ]
            }
        ),
        flush=True,
    )
    return int(
        summary["status"] != "completed"
        or summary["report_status"] != "completed"
        or not summary["index_unchanged"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
