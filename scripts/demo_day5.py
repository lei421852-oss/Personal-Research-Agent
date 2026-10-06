"""四条脚本决策与受控证据下的有限步 Agent Trace，不声称真实排序效果。"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.loop import BoundedToolAgent
from app.evidence.corpus import load_paper_corpus
from app.evidence.models import RetrievedEvidence
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from app.vision.client import VLMResponse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts"


def action(identity, name, **arguments):
    return LLMResponse(
        tool_calls=[ToolCall(id=identity, name=name, arguments=arguments)]
    )


class ControlledRetrieval:
    """显式测试 fixture，不修改真实嵌入或检索排序。"""

    def __init__(self, corpus):
        self.corpus = corpus

    def search_text(self, query, paper_ids, top_k):
        if query == "unmatched first query":
            return []
        items = [
            e
            for e in self.corpus.text_evidence
            if paper_ids is None or e.paper_id in paper_ids
        ]
        if "complexity" in query:
            items = [e for e in items if "reduce" in e.text and "complexity" in e.text]
        return [
            RetrievedEvidence(evidence=e, retrieval_score=1.0) for e in items[:top_k]
        ]


class ControlledVLM:
    def analyze(self, image_path, prompt):
        identity = prompt.split("Evidence ID: ")[1].splitlines()[0]
        return VLMResponse(
            text=json.dumps(
                {
                    "observations": ["The supplied figure is a pipeline schematic."],
                    "interpretation": "The figure describes a pipeline; this schematic alone does not establish superior performance.",
                    "evidence_used": [identity],
                    "limitations": [
                        "Synthetic VLM output for workflow verification only."
                    ],
                }
            )
        )


def last_observation(messages):
    assert messages[-1]["role"] == "tool", (
        "Previous observation must reach the next model decision"
    )
    return json.loads(messages[-1]["content"])


def run_cases(corpus):
    def finish_text(messages, tools):
        observation = last_observation(messages)
        assert observation["status"] == "success"
        evidence = observation["payload"]["evidence"][0]
        return action(
            "text-finish",
            "finish",
            answer=evidence["text"],
            evidence_ids=[evidence["evidence_id"]],
            limitations=["Controlled retrieval; no real ranking measurement."],
        )

    def analyze(messages, tools):
        observation = last_observation(messages)
        figure = observation["payload"]["figure"]
        return action(
            "figure-analyze",
            "analyze_figure",
            question="Does this figure prove superiority?",
            figure_evidence_id=figure["evidence_id"],
            context_evidence_ids=[],
        )

    def finish_figure(messages, tools):
        observation = last_observation(messages)
        analysis = observation["payload"]["analysis"]
        return action(
            "figure-finish",
            "finish",
            answer=analysis["interpretation"],
            evidence_ids=analysis["evidence_used"],
            limitations=analysis["limitations"],
        )

    def revise(messages, tools):
        observation = last_observation(messages)
        assert observation["status"] == "empty"
        return action(
            "revised-search",
            "search_text",
            query="Perceiver Resampler reduce cross-attention complexity",
            paper_ids=["flamingo"],
            top_k=1,
        )

    cases = [
        (
            "Text QA",
            "What does the prepared CLIP text say?",
            [
                action(
                    "text-search",
                    "search_text",
                    query="CLIP objective",
                    paper_ids=["clip"],
                    top_k=1,
                ),
                finish_text,
            ],
        ),
        (
            "Figure QA",
            "Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。",
            [
                action(
                    "figure-find", "find_figure", paper_id="blip", figure_id="Figure 3"
                ),
                analyze,
                finish_figure,
            ],
        ),
        (
            "Insufficient then adjust",
            "Why does Flamingo use the Perceiver Resampler?",
            [
                action(
                    "first-search",
                    "search_text",
                    query="unmatched first query",
                    paper_ids=["flamingo"],
                    top_k=1,
                ),
                revise,
                finish_text,
            ],
        ),
        (
            "Step Exhaustion",
            "Keep requesting context beyond the budget",
            [
                action(f"exhaust-{i}", "read_page", paper_id="flamingo", page=page)
                for i, page in enumerate([4, 9, 10, 11, 4, 9, 10], 1)
            ],
        ),
    ]
    reports = []
    for name, question, responses in cases:
        executor = ToolExecutor(
            create_paper_registry(
                corpus,
                ControlledRetrieval(corpus),
                analyzer=FigureAnalyzer(ControlledVLM()),
            )
        )
        llm = ScriptedLLMClient(responses)
        result = BoundedToolAgent(llm, executor, EvidenceResolver(corpus)).run(question)
        reports.append(
            {
                "case": name,
                "backend": "scripted_llm + controlled_retrieval + fake_vlm",
                "result": result.model_dump(mode="json"),
                "model_requests": llm.calls,
            }
        )
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    reports = run_cases(load_paper_corpus(ROOT / "data/papers"))
    stamp = datetime.now(ZoneInfo("Asia/Shanghai"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = (
        args.output_dir
        / f"{stamp:%Y-%m-%d}-多模态论文研究智能体-Day5-四条完整AgentTrace.json"
    )
    path.write_text(
        json.dumps(
            {
                "note": "Controlled test evidence, not real model decisions, semantic evaluation or embedding ranking.",
                "cases": reports,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for report in reports:
        r = report["result"]
        print(
            f"{report['case']}: {r['status']}, N_tools={r['tool_calls']}, N_model={r['model_calls']}"
        )
    print(path)


if __name__ == "__main__":
    main()
