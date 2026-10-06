"""Thin adapter to the unchanged bounded Agent; no evaluation-time policy fixes."""

from time import perf_counter

from app.agent.loop import BoundedToolAgent
from app.evidence.resolver import EvidenceResolver
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from eval.runners.base import build_run, measured_clients
from eval.runners.text_rag import TextRAGRunner
from eval.schemas import EvaluationCase, EvaluationRun


class MultimodalAgentRunner(TextRAGRunner):
    system = "multimodal_agent"

    def run(self, case: EvaluationCase) -> EvaluationRun:
        if case.annotation_status != "approved":
            raise ValueError("Evaluation requires approved cases")
        with measured_clients(self.resources) as (llm, vision):
            started = perf_counter()
            executor = ToolExecutor(
                create_paper_registry(
                    self.resources.corpus,
                    self.resources.retrieval,
                    analyzer=FigureAnalyzer(vision),
                )
            )
            result = BoundedToolAgent(
                llm, executor, EvidenceResolver(self.resources.corpus)
            ).run(case.question)
            # Finish observations are not domain calls; pair only actual executor traces.
            observations = [
                o for o in result.state.observations if o.tool_name != "finish"
            ]
            return build_run(
                case,
                self.system,
                self.resources,
                status=result.status,
                answer=result.answer,
                citations=result.citations,
                limitations=result.limitations,
                traces=result.state.trace,
                observations=observations,
                llm=llm,
                vision=vision,
                latency_ms=(perf_counter() - started) * 1000,
                figure_k=self.figure_k,
                extra_errors=[result.stop_details] if result.stop_details else [],
            )
