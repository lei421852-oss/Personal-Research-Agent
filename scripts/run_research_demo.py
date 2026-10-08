"""薄封装真实 Planner 与冻结研究链；默认预检，不补建索引，不选择成功重跑。"""

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from app.evidence.corpus import load_paper_corpus
from app.papers.catalog import load_paper_catalog
from app.research.corpus_validation import (
    canonical_hash,
    corpus_snapshot,
    validate_index_records,
)
from app.research.planner import ResearchPlanner
from app.research.requirements import expand_requirements
from app.research.runtime import ResearchRuntimeConfig
from app.research.synthesis_models import ResearchReport
from app.tools.redaction import public_value
from eval.cross_paper import CrossPaperCase, audit_report
from scripts.evaluate_cross_paper import index_records, run_case
from scripts.smoke_research_synthesis import render_markdown
from scripts.verify_worker_coverage_baseline import verify

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTION = (
    "Compare the visual-language connection mechanisms in BLIP-2 and Flamingo. "
    "Explain how each paper transfers visual features into its language model, "
    "covering BLIP-2's Q-Former and visual-language interface, and Flamingo's "
    "Perceiver Resampler and gated cross-attention. Identify the main differences "
    "in visual-language interaction, and cite prepared paper evidence for each "
    "finding. Mark unsupported details as evidence gaps."
)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def index_fingerprint(store):
    """比较索引逻辑内容，不将 Chroma 打开时的内部文件变化误称为重建。"""
    data = store.collection.get(include=["documents", "metadatas", "embeddings"])
    records = [
        {"id": i, "text": t, "metadata": m, "embedding": v.tolist()}
        for i, t, m, v in zip(
            data["ids"],
            data["documents"],
            data["metadatas"],
            data["embeddings"],
            strict=True,
        )
    ]
    return canonical_hash(sorted(records, key=lambda row: row["id"]))


def check_existing_index(config, corpus, snapshot, store):
    expected = {
        "corpus_snapshot_id": snapshot["snapshot_id"],
        "embedding_model": config["embedding_model"],
        "distance": config["distance"],
        "schema_version": 1,
    }
    if not store.collection.count() or store.collection.metadata != expected:
        raise ValueError("existing_index_missing_or_binding_mismatch")
    validate_index_records(corpus, index_records(store))


class RecordingRetriever:
    """透明旁录 query、范围和真实命中；不修改查询、排序或工具契约。"""

    def __init__(self, retriever):
        self.retriever, self.records = retriever, []

    def search_text(self, query, paper_ids=None, top_k=3):
        started = perf_counter()
        row = {"query": query, "paper_ids": paper_ids, "top_k": top_k}
        try:
            hits = self.retriever.search_text(query, paper_ids, top_k)
            row.update(status="success", results=[h.model_dump() for h in hits])
            return hits
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__)
            raise
        finally:
            row["latency_ms"] = (perf_counter() - started) * 1000
            self.records.append(public_value(row))


def preflight(args):
    """只使用当前模型缓存和既有 collection；条件不足直接阻塞。"""
    from dotenv import load_dotenv

    from app.retrieval.embedding import QwenEmbeddingModel
    from app.retrieval.retriever import TextRetriever
    from app.retrieval.vector_store import ChromaTextVectorStore

    if (
        not args.question.strip()
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        raise ValueError("invalid_question_or_timeout")
    integrity = verify(ROOT / "docs/acceptance/stage_f3_1/baseline.json")
    if integrity["status"] != "PASS":
        raise ValueError("frozen_or_f2_artifact_changed")
    g1 = json.loads((ROOT / "docs/acceptance/stage_g1/baseline.json").read_text())
    for item in g1["f3_protected_artifacts"]:
        path = ROOT / item["path"]
        # 新 checkout 没有 ignored 运行副本，仍核验已提交镜像；本机存在则必须匹配。
        if item["path"].startswith("artifacts/") and not path.exists():
            continue
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise ValueError("f3_protected_artifact_changed")
    config = json.loads(args.config.read_text())
    ResearchRuntimeConfig(**config["runtime"])
    corpus = load_paper_corpus(
        ROOT / config["papers_dir"],
        additional_papers_dir=ROOT / config["additional_papers_dir"],
    )
    snapshot = corpus_snapshot(
        ROOT / config["papers_dir"], corpus, ROOT / config["additional_papers_dir"]
    )
    if snapshot["snapshot_id"] != config["corpus_snapshot_id"]:
        raise ValueError("corpus_snapshot_mismatch")
    catalog = load_paper_catalog()
    available = {e.paper_id for e in corpus.text_evidence}
    if (
        len(args.paper_ids) < 2
        or len(set(args.paper_ids)) != len(args.paper_ids)
        or any(p not in available or p not in catalog.documents for p in args.paper_ids)
    ):
        raise ValueError("paper_identity_or_prepared_evidence_unavailable")
    load_dotenv(args.env_file, override=False)
    required = [
        f"{kind}_{field}"
        for kind in ("LLM", "VLM")
        for field in ("API_KEY", "MODEL", "BASE_URL")
    ]
    if any(not os.environ.get(name) for name in required):
        raise ValueError("provider_configuration_incomplete")
    index_dir = ROOT / config["index_dir"]
    if not (index_dir / "chroma.sqlite3").is_file():
        raise ValueError("existing_index_unavailable")
    store = ChromaTextVectorStore(index_dir, config["collection"], create=False)
    check_existing_index(config, corpus, snapshot, store)
    fingerprint = index_fingerprint(store)
    # 本演示不自动下载新模型；缓存不足必须报告阻塞。
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    embedding = QwenEmbeddingModel(config["embedding_model"], device=config["device"])
    vector = embedding.embed_query("Check local embedding availability.")
    if not vector or not all(math.isfinite(v) for v in vector):
        raise ValueError("embedding_unavailable")
    report = {
        "status": "ready",
        "question": args.question,
        "paper_ids": args.paper_ids,
        "configuration": config,
        "corpus_snapshot": snapshot,
        "index_fingerprint": fingerprint,
        "index_record_count": store.collection.count(),
        "embedding_dimension": len(vector),
        "preflight_embedding_calls": 1,
        "models": {
            kind.lower(): {
                "model": os.environ[f"{kind}_MODEL"],
                "provider_host": urlparse(os.environ[f"{kind}_BASE_URL"]).hostname,
            }
            for kind in ("LLM", "VLM")
        },
        "provider_calls": 0,
        "index_rebuilt": False,
        "worker": "PaperResearchWorker",
        "coverage_worker_enabled": False,
        "case_timeout_seconds": args.timeout,
    }
    return (
        report,
        corpus,
        snapshot,
        config,
        store,
        TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases),
    )


async def execute_case(case, corpus, snapshot, config, retriever, timeout):
    cancelled = {}

    async def tracked():
        try:
            return await run_case(
                case, corpus, snapshot, config, scripted=False, retriever=retriever
            )
        except asyncio.CancelledError as exc:
            cancelled.update(getattr(exc, "evaluation_snapshot", {}))
            raise

    try:
        return await asyncio.wait_for(tracked(), timeout)
    except TimeoutError:
        return cancelled | {"status": "timed_out", "issue_type": "TimeoutError"}


def write_display(output, execution, retrievals, corpus):
    """展示内容只渲染实际报告；核验证据身份，不代填语义评分。"""
    raw = execution.get("research", {})
    report_data = (raw.get("outcome") or {}).get("report")
    save(output / "retrievals.json", retrievals)
    save(output / "execution.json", public_value(execution))
    (output / "question.md").write_text("# 研究问题\n\n" + execution["question"] + "\n")
    if not report_data:
        (output / "research-report.md").write_text(
            "# 研究未生成报告\n\n本次真实执行未返回可交付报告；原因和轨迹见 execution.json。\n"
        )
        (output / "evidence-map.md").write_text(
            "# 没有可展示的已审核报告\n\n实际检索记录保留在 retrievals.json；不生成虚构结论与引用映射。\n"
        )
        return {"report_status": "not_generated", "citation_check": "not_available"}
    report = ResearchReport.model_validate(report_data)
    from app.research.synthesis_models import SynthesisInput

    source = SynthesisInput.model_validate(raw["outcome"]["synthesis_input"])
    structural = audit_report(source, report)
    corpus_by_id = {
        e.evidence_id: e for e in corpus.text_evidence + corpus.figure_evidence
    }
    lines = [
        "# 已审核论断与原始证据映射",
        "",
        "论断正文来自本次模型 Worker；引用身份、范围与报告结构由程序核验。",
        "Reviewer passed 是模型语义判断。独立人工事实审核：尚未完成。",
        "",
    ]
    for claim in report.verified_claims:
        lines.extend(
            [
                f"## {claim.paper_id} / {claim.dimension_id}",
                "",
                f"Claim ID: `{claim.claim_id}` / Requirement: `{claim.requirement_id}`",
                "",
                claim.text,
                "",
            ]
        )
        for evidence in claim.evidence:
            original = corpus_by_id[evidence.evidence_id]
            if (
                original.paper_id != claim.paper_id
                or original.text != evidence.text
                or original.page != evidence.page
            ):
                raise ValueError("display_evidence_identity_mismatch")
            lines.extend(
                [
                    f"### `{original.evidence_id}` · PDF Page {original.page}",
                    "",
                    "原始 Prepared Evidence（逐字摘录）：",
                    "",
                    "```text",
                    original.text,
                    "```",
                    "",
                ]
            )
    lines.extend(["## 保留的缺失单元", ""])
    lines.extend(
        f"- {m.paper_id} / {m.dimension_id}: `{m.status}`"
        for m in report.missing_evidence
    )
    (output / "evidence-map.md").write_text("\n".join(lines) + "\n")
    (output / "research-report.md").write_text(render_markdown(report))
    save(output / "research-report.json", report.model_dump(mode="json"))
    return {"report_status": report.status, "citation_check": "passed", **structural}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        default=["blip-2-2301.12597v1", "flamingo-2204.14198v1"],
    )
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/cross_paper_eval.json"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    if (output / "execution.json").exists() or (
        output / "execution-started.json"
    ).exists():
        parser.error("Output already contains an execution; use a new directory")
    started = perf_counter()
    try:
        ready, corpus, snapshot, config, store, retriever = preflight(args)
    except Exception as exc:  # noqa: BLE001 - 预检失败不调用 Provider、不隐式修复
        save(
            output / "preflight.json",
            {
                "status": "blocked",
                "issue_type": type(exc).__name__,
                "issue": public_value(str(exc)),
                "provider_calls": 0,
            },
        )
        print("blocked: inspect preflight.json", flush=True)
        return 2
    save(output / "preflight.json", ready)
    if not args.execute:
        print("ready: preflight only; no Provider calls or index rebuild", flush=True)
        return 0
    # 独占运行标记防止同一路径重跑覆盖；预检失败不占用正式运行机会。
    with (output / "execution-started.json").open("x") as stream:
        json.dump(
            {"question": args.question, "formal_attempt": 1}, stream, ensure_ascii=False
        )
    from app.llm.provider import OpenAICompatibleLLMClient

    client = None
    recording = RecordingRetriever(retriever)
    execution = {
        "question": args.question,
        "paper_ids": args.paper_ids,
        "preflight": ready,
        "effective_planner_mode": "real_planner_once_then_default_run_case",
    }
    research_started = perf_counter()
    try:
        client = OpenAICompatibleLLMClient(
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=config["runtime"]["operation_timeout_seconds"],
        )
        planner = ResearchPlanner(client, load_paper_catalog())
        preparation = planner.prepare(args.question, args.paper_ids)
        execution["preparation"] = preparation.model_dump(mode="json")
        plan = preparation.plan
        case = CrossPaperCase(
            case_id="g2-blip2-flamingo-demo",
            category="two_paper",
            plan=plan,
            expected_behavior="evidence_grounded_demo",
            source_labels={r.requirement_id: [] for r in expand_requirements(plan)},
        )
        print(
            f"Planner prepared {len(plan.dimensions)} dimensions; running default research chain",
            flush=True,
        )
        execution["research"] = asyncio.run(
            execute_case(case, corpus, snapshot, config, recording, args.timeout)
        )
    except Exception as exc:  # noqa: BLE001 - 完整保留唯一一次演示的失败
        execution["failure"] = {
            "issue_type": type(exc).__name__,
            "issue": public_value(str(exc)),
        }
    finally:
        planner_events = client.calls if client is not None else []
        execution["planner_events"] = public_value(planner_events)
        execution["planner_model_calls"] = len(planner_events)
        execution["research_latency_ms"] = (perf_counter() - research_started) * 1000
        execution["total_latency_ms"] = (perf_counter() - started) * 1000
        if client is not None:
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
            "planner_model_calls": len(planner_events),
            "llm_provider_attempts_observed": len(planner_events)
            + raw.get("llm_provider_attempts_observed", 0),
            "research_metrics": raw.get("metrics"),
            "retrieval_attempts": len(recording.records),
            "research_latency_ms": execution["research_latency_ms"],
            "total_latency_ms": execution["total_latency_ms"],
            "index_unchanged": execution["index_unchanged"],
            "worker": "PaperResearchWorker",
            "coverage_worker_enabled": False,
            "independent_human_review": "not_performed",
            "configuration": ready,
            "counting_note": "LLM counts include physical retries; Worker budget model_calls may reserve VLM attempts. Planner and preflight costs are separate.",
        }
        save(output / "execution-summary.json", summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "status",
                    "report_status",
                    "llm_provider_attempts_observed",
                    "index_unchanged",
                )
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
