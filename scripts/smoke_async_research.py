"""由用户显式运行的真实 Worker Pool smoke；复用已生成计划，不调用 Planner。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.papers.catalog import load_paper_catalog
from app.research.async_tools import AsyncLLMAdapter, AsyncToolExecutorAdapter
from app.research.models import ResearchPlan
from app.research.orchestrator import AsyncResearchOrchestrator
from app.research.requirements import expand_requirements, group_worker_tasks
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.worker import PaperResearchWorker

ROOT = Path(__file__).resolve().parents[1]


def load_worker_tasks(path: Path, case_id: str | None = None):
    """接受 ResearchPlan、ResearchPreparation 或既有 Planner smoke 的一个案例。"""
    data = json.loads(path.read_text())
    if "cases" in data:
        cases = data["cases"]
        if case_id is None and len(cases) != 1:
            raise ValueError("Select --case-id when the file contains multiple plans")
        selected = next(
            (c for c in cases if case_id is None or c.get("case_id") == case_id), None
        )
        if selected is None:
            raise ValueError("Requested plan case does not exist")
        data = {key: selected[key] for key in ("question", "paper_ids", "dimensions")}
    elif "plan" in data:
        data = data["plan"]
    plan = ResearchPlan.model_validate(data)
    return plan, group_worker_tasks(expand_requirements(plan))


async def run_real_smoke(plan, tasks, config, output):
    """共享只读语料/检索模型；每个 Worker 独占 Provider 和 ToolExecutor。"""
    from app.llm.provider import OpenAICompatibleLLMClient
    from app.papers.evidence_access import DiscoveryEvidenceRepository
    from app.retrieval.embedding import QwenEmbeddingModel
    from app.retrieval.retriever import TextRetriever
    from app.retrieval.vector_store import ChromaTextVectorStore
    from app.tools.discovery import DiscoveryScopedTextTool
    from app.tools.executor import ToolExecutor
    from app.tools.figure import AnalyzeFigureTool
    from app.tools.paper import FindFigureTool, ReadPageTool
    from app.tools.registry import ToolRegistry
    from app.vision.analysis import FigureAnalyzer
    from app.vision.provider import OpenAICompatibleVisionClient

    corpus = load_paper_corpus(ROOT / "data/papers")
    catalog = load_paper_catalog()
    resolver = EvidenceResolver(corpus, paper_catalog=catalog)
    embedding = QwenEmbeddingModel(
        os.environ.get("AGENT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
        device=os.environ.get("AGENT_DEVICE", "cpu"),
    )
    store = ChromaTextVectorStore(
        os.environ.get("AGENT_INDEX_DIR", str(ROOT / "data/chroma/day2")),
        "papers-day2-text",
    )
    retrieval = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
    gate = ResourceGate(config)
    workers, clients = [], []

    def factory(task):
        llm = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=config.operation_timeout_seconds,
        )
        clients.append(llm)
        vision = OpenAICompatibleVisionClient(
            api_key=os.environ["VLM_API_KEY"],
            model=os.environ["VLM_MODEL"],
            base_url=os.environ["VLM_BASE_URL"],
            timeout=config.operation_timeout_seconds,
        )
        clients.append(vision)
        papers = DiscoveryEvidenceRepository(corpus, catalog)
        registry = ToolRegistry()
        registry.register(DiscoveryScopedTextTool(retrieval, papers, catalog))
        registry.register(FindFigureTool(papers))
        registry.register(ReadPageTool(papers))
        registry.register(AnalyzeFigureTool(resolver, FigureAnalyzer(vision)))
        worker = PaperResearchWorker(
            AsyncLLMAdapter(llm, gate),
            AsyncToolExecutorAdapter(ToolExecutor(registry), gate),
            resolver,
            config,
        )
        workers.append(worker)
        return worker

    orchestrator = AsyncResearchOrchestrator(factory, config, gate)
    status = "completed"
    report = None
    try:
        results = await orchestrator.run(tasks)
    except asyncio.CancelledError:
        results = orchestrator.last_results
        status = "cancelled"
        raise
    finally:
        if orchestrator.trace is not None:
            results = orchestrator.last_results
            report = {
                "mode": "real_prepared_corpus_worker_smoke",
                "status": status,
                "question": plan.question,
                "paper_ids": plan.paper_ids,
                "configuration": config.model_dump(mode="json"),
                "paper_evidence_availability": resolver.paper_catalog,
                "worker_results": [r.model_dump(mode="json") for r in results],
                "orchestrator_trace": orchestrator.trace.model_dump(mode="json"),
                "serial_estimated_latency_ms": sum(r.latency_ms for r in results),
                "actual_parallel_latency_ms": orchestrator.trace.total_latency_ms,
                "latency_protocol": "Sum of observed worker latencies is a serial proxy, includes concurrent resource waiting; not a measured serial run or benchmark.",
                "real_smoke_executed": True,
                "limitations": [
                    "Only prepared text/figure evidence is citable; discovery-only papers may complete with insufficient evidence.",
                    "model_calls conservatively counts LLM calls plus VLM tool attempts; preprocessing failures can reserve model budget without invoking the VLM.",
                    "Cancelled blocking threads may continue until their own operation timeout; result metrics are snapshots, not final provider billing.",
                ],
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        # 外层取消不能强杀在飞线程；关闭其私有客户端前等 Provider 自身超时/完成。
        cleanup_started = perf_counter()
        try:
            await asyncio.gather(*(worker.drain() for worker in workers))
        finally:
            for client in clients:
                client.close()
        if report is not None:
            report["cleanup_latency_ms"] = (perf_counter() - cleanup_started) * 1000
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "worker_statuses": [r.status.value for r in results],
                "trace": orchestrator.trace.model_dump(mode="json"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/async-research-smoke.json"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验已有计划和配置，不初始化 Provider、模型或执行 Worker",
    )
    flags = {
        "max-workers": "max_concurrent_workers",
        "max-llm": "max_concurrent_llm",
        "max-vlm": "max_concurrent_vlm",
        "max-retrieval": "max_concurrent_retrieval",
        "max-tool-calls": "max_tool_calls_per_worker",
        "max-model-calls": "max_model_calls_per_worker",
        "max-retries": "max_retries",
    }
    for flag in flags:
        parser.add_argument(f"--{flag}", type=int)
    parser.add_argument("--operation-timeout", type=float)
    args = parser.parse_args()
    overrides = {
        field: getattr(args, flag.replace("-", "_"))
        for flag, field in flags.items()
        if getattr(args, flag.replace("-", "_")) is not None
    }
    if args.operation_timeout is not None:
        overrides["operation_timeout_seconds"] = args.operation_timeout
    config = ResearchRuntimeConfig(**overrides)
    plan, tasks = load_worker_tasks(args.plan, args.case_id)
    catalog = load_paper_catalog()
    if any(p not in catalog.documents for p in plan.paper_ids):
        parser.error("Plan contains a paper outside the current Paper Catalog")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.validate_only:
        args.output.write_text(
            json.dumps(
                {
                    "mode": "validated_not_executed",
                    "real_smoke_executed": False,
                    "plan": plan.model_dump(mode="json"),
                    "worker_tasks": [t.model_dump(mode="json") for t in tasks],
                    "configuration": config.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(f"Validated {len(tasks)} worker tasks; no model or worker execution.")
        return
    from dotenv import load_dotenv

    if not args.env_file.is_file():
        parser.error("Configured environment file does not exist")
    load_dotenv(args.env_file, override=False)
    if any(
        not os.environ.get(f"{kind}_{field}")
        for kind in ("LLM", "VLM")
        for field in ("API_KEY", "MODEL", "BASE_URL")
    ):
        parser.error("Real smoke requires configured LLM/VLM credentials")
    asyncio.run(run_real_smoke(plan, tasks, config, args.output))


if __name__ == "__main__":
    main()
