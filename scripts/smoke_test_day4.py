"""Run two real fixed multimodal QA cases; requires explicit VLM environment configuration."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.qa.service import MultimodalQAService
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from app.vision.provider import OpenAICompatibleVisionClient

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("/mnt/d/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体")


class UnusedRetrieval:
    def search_text(self, *args):
        raise RuntimeError("Fixed workflow does not retrieve dynamically")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    required = ["VLM_API_KEY", "VLM_MODEL", "VLM_BASE_URL"]
    if not all(os.environ.get(k) for k in required):
        parser.error(
            "Configure VLM_API_KEY, VLM_MODEL and VLM_BASE_URL in this process environment; no credentials are read from other projects."
        )
    corpus = load_paper_corpus(ROOT / "data/papers")
    client = OpenAICompatibleVisionClient(
        api_key=os.environ["VLM_API_KEY"],
        model=os.environ["VLM_MODEL"],
        base_url=os.environ["VLM_BASE_URL"],
    )
    executor = ToolExecutor(
        create_paper_registry(
            corpus, UnusedRetrieval(), analyzer=FigureAnalyzer(client)
        )
    )
    service = MultimodalQAService(executor, EvidenceResolver(corpus))
    cases = [
        ("blip", "Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。"),
        (
            "flamingo",
            "Explain how Figure 3 of Flamingo connects visual inputs to the language model. Use only the supplied figure, caption and context; explicitly identify unsupported parts.",
        ),
    ]
    results = []
    try:
        for paper, question in cases:
            result = service.answer(question, paper, "Figure 3")
            results.append(
                {
                    "paper": paper,
                    "question": question,
                    "result": result.model_dump(mode="json"),
                    "grounding_review": "pending; review visual observations and evidence support manually",
                }
            )
            print(
                f"{paper}: status={result.status}, tools={result.tool_calls}, citations_validated={result.citations_validated}"
            )
    finally:
        client.close()
    stamp = datetime.now(ZoneInfo("Asia/Shanghai"))
    output = (
        args.output_dir
        / f"{stamp:%Y-%m-%d}-多模态论文研究智能体-Day4-真实多模态测试-{stamp:%H%M%S-%f}.json"
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"model": os.environ["VLM_MODEL"], "agent_loop": False, "cases": results},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved: {output}")
    if any(case["result"]["status"] != "success" for case in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
