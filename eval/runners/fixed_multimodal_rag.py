"""明确 Figure 的问题复用 Day 4 固定服务，其余输入使用固定文本 RAG。"""

from time import perf_counter

from app.evidence.resolver import EvidenceResolver
from app.qa.service import MultimodalQAService
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from eval.runners.base import build_run, figure_from_question, measured_clients
from eval.runners.text_rag import TextRAGRunner
from eval.schemas import EvaluationCase, EvaluationRun


class FixedMultimodalRAGRunner(TextRAGRunner):
    system = "fixed_multimodal_rag"

    def run(self, case: EvaluationCase) -> EvaluationRun:
        if case.annotation_status != "approved":
            raise ValueError("Evaluation requires approved cases")
        try:
            target = figure_from_question(case.question, self.resources.corpus)
        except ValueError:
            with measured_clients(self.resources) as (llm, vision):
                started = perf_counter()
                return build_run(
                    case,
                    self.system,
                    self.resources,
                    status="unsupported",
                    answer=None,
                    citations=[],
                    limitations=[
                        "Fixed baseline cannot resolve a unique figure from the question."
                    ],
                    traces=[],
                    observations=[],
                    llm=llm,
                    vision=vision,
                    latency_ms=(perf_counter() - started) * 1000,
                    figure_k=self.figure_k,
                )
        if target is None:
            return super().run(case)
        with measured_clients(self.resources) as (llm, vision):
            started = perf_counter()
            executor = ToolExecutor(
                create_paper_registry(
                    self.resources.corpus,
                    self.resources.retrieval,
                    analyzer=FigureAnalyzer(vision),
                )
            )
            service = MultimodalQAService(
                executor, EvidenceResolver(self.resources.corpus)
            )
            # 不读取标注中的 target_figures、relevant_evidence_ids 或最佳上下文页。
            result = service.answer(case.question, target[0], target[1])
            status = {
                "success": "completed",
                "empty": "insufficient_evidence",
                "error": "tool_failure",
            }[result.status]
            return build_run(
                case,
                self.system,
                self.resources,
                status=status,
                answer=result.answer,
                citations=result.citations,
                limitations=result.limitations,
                traces=result.trace,
                observations=result.observations,
                llm=llm,
                vision=vision,
                latency_ms=(perf_counter() - started) * 1000,
                figure_k=self.figure_k,
            )
