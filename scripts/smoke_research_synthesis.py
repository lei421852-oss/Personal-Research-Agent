"""消费已保存的 Stage A/B 结果，生成综合报告；不启动新的研究或审核。"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.research.async_tools import AsyncLLMAdapter
from app.research.models import WorkerResult
from app.research.review_models import ReviewResult
from app.research.runtime import ResourceGate
from app.research.synthesis_agent import SynthesisAgent, SynthesisConfig
from app.research.synthesis_input import SynthesisInputBuilder, SynthesisInputError
from app.research.synthesis_runtime import SynthesisRuntime
from scripts.smoke_async_research import ROOT, load_worker_tasks


def render_markdown(report):
    """正文逐字保留权威 Claim；不让模型替缺失单元补写技术事实。"""
    claims = {c.claim_id: c for c in report.verified_claims}
    lines = [
        f"# {report.title}",
        "",
        report.question,
        "",
        f"Status: `{report.status}`",
        "",
        report.summary,
        "",
    ]
    for section in report.sections:
        lines.extend([f"## {section.title}", ""])
        for cell in section.cells:
            lines.extend(
                [
                    f"### {cell.paper_id}",
                    "",
                    f"Requirement: `{cell.requirement_id}` · Status: `{cell.status}`",
                    "",
                ]
            )
            if cell.claim_id:
                claim = claims[cell.claim_id]
                lines.extend(
                    [claim.text, "", f"Claim: `{claim.claim_id}`", "", "Sources:", ""]
                )
                lines.extend(
                    f"- `{e.evidence_id}` · {e.paper_id} · Page {e.page}"
                    + (f" · {e.figure_id}" if e.figure_id else "")
                    for e in claim.evidence
                )
                lines.append("")
            else:
                lines.extend(["没有该单元的已审核主张；不补充论文技术事实。", ""])
        for comparison in section.comparisons:
            lines.extend(
                [
                    "Side-by-side verified claims: "
                    + ", ".join(f"`{identity}`" for identity in comparison.claim_ids),
                    "",
                ]
            )
    lines.extend(
        [
            "## Metrics",
            "",
            "```json",
            report.metrics.model_dump_json(indent=2),
            "```",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.limitations)
    if report.issue:
        lines.extend(
            ["", f"Execution issue: `{report.issue.code}` — {report.issue.message}"]
        )
    return "\n".join(lines) + "\n"


async def run_real(runtime, plan, workers, reviews, output, markdown, metadata):
    try:
        await runtime.run(plan, workers, reviews)
    finally:
        try:
            if runtime.last_report is not None:
                payload = metadata | {
                    "research_report": runtime.last_report.model_dump(mode="json")
                }
                output.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
                )
                markdown.write_text(render_markdown(runtime.last_report))
        finally:
            await runtime.drain()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--worker-results", required=True, type=Path)
    parser.add_argument("--review-results", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/research-synthesis.json"
    )
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--max-model-calls", type=int, default=5)
    parser.add_argument("--max-protocol-corrections", type=int, default=2)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    config = SynthesisConfig(
        max_model_calls=args.max_model_calls,
        max_protocol_corrections=args.max_protocol_corrections,
    )
    plan, _ = load_worker_tasks(args.plan, args.case_id)
    worker_bytes = args.worker_results.read_bytes()
    worker_data = json.loads(worker_bytes)
    review_data = json.loads(args.review_results.read_text())
    source_hash = hashlib.sha256(worker_bytes).hexdigest()
    if review_data.get("source_worker_report_sha256") != source_hash:
        raise SynthesisInputError("worker_review_source_mismatch")
    workers = [WorkerResult.model_validate(w) for w in worker_data["worker_results"]]
    reviews = [ReviewResult.model_validate(r) for r in review_data["results"]]
    builder = SynthesisInputBuilder(
        EvidenceResolver(load_paper_corpus(ROOT / "data/papers"))
    )
    source = builder.build(plan, workers, reviews)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    markdown = args.markdown or args.output.with_suffix(".md")
    markdown.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "mode": "real_research_synthesis_smoke",
        "configuration": config.model_dump(mode="json"),
        "worker_report_sha256": source_hash,
        "review_report_sha256": hashlib.sha256(
            args.review_results.read_bytes()
        ).hexdigest(),
        "input_counts": {
            "cells": len(source.cells),
            "verified": len(source.verified_claims),
            "missing": len(source.missing_evidence),
        },
    }
    if args.validate_only:
        args.output.write_text(
            json.dumps(
                metadata | {"mode": "validated_not_executed", "real_model_calls": 0},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(
            "Validated synthesis sources; no Provider, Planner, Worker or Reviewer execution."
        )
        return
    client = None
    adapter = None
    if source.verified_claims:
        import os

        from dotenv import load_dotenv

        from app.llm.provider import OpenAICompatibleLLMClient

        load_dotenv(args.env_file, override=True)
        client = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=60,
        )
        adapter = AsyncLLMAdapter(client, ResourceGate())
        metadata["llm_model"] = os.environ["LLM_MODEL"]
    runtime = SynthesisRuntime(builder, SynthesisAgent(adapter, config))
    try:
        asyncio.run(
            run_real(runtime, plan, workers, reviews, args.output, markdown, metadata)
        )
    finally:
        if client:
            client.close()
    print(runtime.last_report.metrics.model_dump_json(), flush=True)


if __name__ == "__main__":
    try:
        main()
    except SynthesisInputError as error:
        print(
            json.dumps(
                {"status": "input_error", "issue": error.issue.model_dump(mode="json")},
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from None
