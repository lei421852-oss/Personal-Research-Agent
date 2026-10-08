"""审核既有真实 Worker 结果；不重跑 Planner/Worker，不生成跨论文答案。"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.papers.catalog import load_paper_catalog
from app.research.async_tools import AsyncLLMAdapter
from app.research.models import WorkerResult
from app.research.review_runtime import EvidenceReviewRuntime, ReviewRuntimeConfig
from app.research.review_validation import DeterministicReviewValidator
from app.research.reviewer import EvidenceReviewer
from app.research.runtime import ResearchRuntimeConfig, ResourceGate

ROOT = Path(__file__).resolve().parents[1]


async def run_review(
    worker_results, validator, review_config, resource_config, output, source_hash
):
    import os

    from app.llm.provider import OpenAICompatibleLLMClient

    gate = ResourceGate(resource_config)
    clients = []

    def factory(task):
        client = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=resource_config.operation_timeout_seconds,
        )
        clients.append(client)
        return EvidenceReviewer(
            AsyncLLMAdapter(client, gate),
            max_model_calls=review_config.max_model_calls_per_review,
        )

    runtime = EvidenceReviewRuntime(factory, validator, review_config, gate)
    cancelled = False
    try:
        await runtime.run(worker_results)
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if runtime.trace is not None:
            report = {
                "mode": "real_evidence_review_smoke",
                "real_smoke_executed": True,
                "cancelled": cancelled,
                "source_worker_report_sha256": source_hash,
                "configuration": review_config.model_dump(mode="json"),
                "resource_configuration": resource_config.model_dump(mode="json"),
                "llm_model": os.environ["LLM_MODEL"],
                "results": [r.model_dump(mode="json") for r in runtime.last_results],
                "trace": runtime.trace.model_dump(mode="json"),
                "limitations": [
                    "Engineering smoke only; no benchmark or Worker recovery loop.",
                    "Observed IDs must originate from trusted runtime records; external JSON is not an authenticated observation ledger.",
                    "Review is based on prepared text/captions/metadata; no additional image interpretation or new evidence retrieval.",
                ],
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        # 与现有适配器一致：先等后台阻塞调用完成，再关闭独占客户端。
        try:
            await runtime.drain()
        finally:
            for client in clients:
                client.close()
    print(
        json.dumps(runtime.trace.model_dump(mode="json"), ensure_ascii=False),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-results", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/evidence-review-smoke.json"
    )
    parser.add_argument("--max-reviewers", type=int, default=2)
    parser.add_argument("--max-llm", type=int, default=2)
    parser.add_argument("--max-model-calls", type=int, default=3)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    review_config = ReviewRuntimeConfig(
        max_concurrent_reviewers=args.max_reviewers,
        max_model_calls_per_review=args.max_model_calls,
    )
    resource_config = ResearchRuntimeConfig(max_concurrent_llm=args.max_llm)
    source = args.worker_results.read_bytes()
    document = json.loads(source)
    workers = [WorkerResult.model_validate(w) for w in document["worker_results"]]
    corpus = load_paper_corpus(ROOT / "data/papers")
    validator = DeterministicReviewValidator(
        EvidenceResolver(corpus, paper_catalog=load_paper_catalog())
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.validate_only:
        # 程序校验不需要凭据或 Provider；不会创建 Reviewer。
        outcomes = [validator.validate(w, r) for w in workers for r in w.requirements]
        from app.research.review_models import ReviewTask

        args.output.write_text(
            json.dumps(
                {
                    "mode": "validated_not_executed",
                    "real_smoke_executed": False,
                    "reviewable_count": sum(
                        isinstance(o, ReviewTask) for o in outcomes
                    ),
                    "bypassed_results": [
                        o.model_dump(mode="json")
                        for o in outcomes
                        if not isinstance(o, ReviewTask)
                    ],
                    "configuration": review_config.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print("Validated existing Worker results; no Reviewer model execution.")
        return
    from dotenv import load_dotenv

    load_dotenv(args.env_file, override=True)
    asyncio.run(
        run_review(
            workers,
            validator,
            review_config,
            resource_config,
            args.output,
            hashlib.sha256(source).hexdigest(),
        )
    )


if __name__ == "__main__":
    main()
