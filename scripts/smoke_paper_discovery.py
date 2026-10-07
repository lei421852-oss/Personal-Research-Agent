"""显式两题真实 Agent 冒烟；只检查发现/范围路径，不执行完整评测。"""

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path

from app.agent.loop import BoundedToolAgent
from app.api.runtime import create_configured_runtime
from app.evidence.resolver import EvidenceResolver
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/paper-discovery-agent-smoke.json",
    )
    args = parser.parse_args()
    os.environ["AGENT_ENV_FILE"] = str(args.env_file.resolve())
    os.environ["AGENT_PAPER_DISCOVERY"] = "1"
    # 使用已有真实正文索引，只查询，不重建或修改其排序。
    os.environ["AGENT_RETRIEVAL_BACKEND"] = "real"
    runtime = create_configured_runtime()
    cases = [
        (
            "open",
            "Find papers on adapting language models to visual few-shot learning. First discover candidates, then inspect prepared text evidence from an appropriate candidate to explain the approach. Use only evidence obtained through tools.",
        ),
        ("known", "What training objective does CLIP use to align images and text?"),
    ]
    reports = []
    for name, question in cases:
        with ExitStack() as stack:
            llm, vision = runtime.client_factory(stack)
            executor = ToolExecutor(
                create_paper_registry(
                    runtime.corpus,
                    runtime.retrieval,
                    analyzer=FigureAnalyzer(vision),
                    paper_retriever=runtime.paper_retriever,
                    paper_catalog=runtime.paper_catalog,
                )
            )
            result = BoundedToolAgent(
                llm,
                executor,
                EvidenceResolver(runtime.corpus, paper_catalog=runtime.paper_catalog),
            ).run(question)
            reports.append(
                {
                    "case": name,
                    "question": question,
                    "backend": "real_llm + real_paper_embedding_chroma + real_text_embedding_chroma",
                    "status": result.status,
                    "answer": result.answer,
                    "citations": [c.model_dump(mode="json") for c in result.citations],
                    "limitations": result.limitations,
                    "tool_calls": result.tool_calls,
                    "model_calls": result.model_calls,
                    "total_latency_ms": result.total_latency_ms,
                    "trace": [t.model_dump(mode="json") for t in result.state.trace],
                    "observations": [
                        o.model_dump(mode="json") for o in result.state.observations
                    ],
                    "stop_details": result.stop_details,
                }
            )
            print(name, result.status, result.tool_calls, result.model_calls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "scope": "Two actual smoke runs; path inspection is not a semantic benchmark.",
                "cases": reports,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
