"""Explicit fake/real dependencies for smoke versus actual evaluation."""

import json
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.llm.client import LLMClient
from app.llm.models import LLMResponse, ToolCall
from eval.runners.base import EvaluationResources, figure_from_question
from scripts.demo_day5 import ControlledRetrieval, ControlledVLM

ROOT = Path(__file__).resolve().parents[2]


class FakeEvaluationLLM(LLMClient):
    def __init__(self, corpus):
        self.corpus = corpus
        self.requests = []
        self.number = 0

    def complete(self, *, messages, tools):
        self.requests.append({"messages": messages, "tools": tools})
        self.number += 1
        offered = {t["name"] for t in tools}
        if offered == {"finish"}:
            request = json.loads(messages[-1]["content"])
            observation = request["observation"]
            ids = [e["evidence_id"] for e in observation["payload"].get("evidence", [])]
            call = ToolCall(
                id=f"fake-{self.number}",
                name="finish",
                arguments={
                    "answer": "Fake grounded response for runner verification only.",
                    "evidence_ids": ids[:1],
                    "limitations": [
                        "Synthetic model output; no semantic performance claim."
                    ],
                },
            )
        elif messages[-1]["role"] == "user":
            question = messages[-1]["content"]
            target = figure_from_question(question, self.corpus)
            name = "find_figure" if target else "search_text"
            arguments = (
                {"paper_id": target[0], "figure_id": target[1]}
                if target
                else {"query": question, "paper_ids": None, "top_k": 3}
            )
            call = ToolCall(id=f"fake-{self.number}", name=name, arguments=arguments)
        else:
            observation = json.loads(messages[-1]["content"])
            payload = observation["payload"]
            if (
                observation["tool_name"] == "find_figure"
                and observation["status"] == "success"
            ):
                call = ToolCall(
                    id=f"fake-{self.number}",
                    name="analyze_figure",
                    arguments={
                        "question": messages[1]["content"],
                        "figure_evidence_id": payload["figure"]["evidence_id"],
                        "context_evidence_ids": [],
                    },
                )
            else:
                ids = [e["evidence_id"] for e in payload.get("evidence", [])]
                if payload.get("figure"):
                    ids.append(payload["figure"]["evidence_id"])
                call = ToolCall(
                    id=f"fake-{self.number}",
                    name="finish",
                    arguments={
                        "answer": "Fake grounded response for runner verification only.",
                        "evidence_ids": ids[:1],
                        "limitations": [
                            "Synthetic model output; no semantic performance claim."
                        ],
                    },
                )
        return LLMResponse(tool_calls=[call])


def create_resources(backend):
    if backend == "real":
        from app.api.runtime import create_configured_runtime

        runtime = create_configured_runtime()
        return EvaluationResources(
            runtime.corpus, runtime.retrieval, runtime.client_factory, "real"
        )
    if backend != "fake":
        raise ValueError("Backend must be fake or real")
    corpus = load_paper_corpus(ROOT / "data/papers")

    def clients(stack):
        return FakeEvaluationLLM(corpus), ControlledVLM()

    return EvaluationResources(corpus, ControlledRetrieval(corpus), clients, "fake")
