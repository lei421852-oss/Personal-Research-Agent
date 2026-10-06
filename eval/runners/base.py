"""Shared measurement/projection, never an alternative Agent controller."""

import re
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

from app.diagnostics import failure_details
from app.evidence.corpus import PaperCorpus
from app.evidence.models import FigureEvidence
from app.llm.client import LLMClient
from app.retrieval.contracts import TextRetrieval
from app.tools.redaction import public_value
from app.vision.client import VisionLanguageClient
from eval.metrics.citation import citation_identity_valid, citation_relevance_precision
from eval.metrics.cost import cost_metrics
from eval.metrics.retrieval import evidence_recall, recall_at_k, unique
from eval.metrics.task_success import initial_task_success
from eval.schemas import EvaluationRun, EvidenceReference


@dataclass
class EvaluationResources:
    corpus: PaperCorpus
    retrieval: TextRetrieval
    client_factory: Callable[[ExitStack], tuple[LLMClient, VisionLanguageClient]]
    backend: str


class MeasuredLLM(LLMClient):
    def __init__(self, client):
        self.client, self.events = client, []

    def complete(self, *, messages, tools):
        start = perf_counter()
        event = {"status": "error"}
        try:
            response = self.client.complete(messages=messages, tools=tools)
            event = {
                "status": "success",
                "actions": public_value(
                    [c.model_dump(mode="json") for c in response.tool_calls]
                ),
            }
            return response
        except Exception as exc:
            event["failure"] = failure_details(
                exc, phase="llm_complete", summary="Evaluation model request failed."
            )
            raise
        finally:
            event["latency_ms"] = (perf_counter() - start) * 1000
            self.events.append(event)


class MeasuredVision:
    def __init__(self, client):
        self.client, self.events = client, []

    def analyze(self, image_path, prompt):
        start = perf_counter()
        event = {"status": "error"}
        try:
            response = self.client.analyze(image_path, prompt)
            event["status"] = "success"
            return response
        except Exception as exc:
            event["failure"] = failure_details(
                exc, phase="vlm_request", summary="Evaluation vision request failed."
            )
            raise
        finally:
            event["latency_ms"] = (perf_counter() - start) * 1000
            self.events.append(event)


@contextmanager
def measured_clients(resources):
    with ExitStack() as stack:
        llm, vision = resources.client_factory(stack)
        yield MeasuredLLM(llm), MeasuredVision(vision)


def figure_from_question(question, corpus):
    """Fixed baseline input extraction only. Never consult gold target_figures or answer points."""
    figures = list(
        dict.fromkeys(re.findall(r"figure\s*(\d+)", question, re.IGNORECASE))
    )
    if not figures:
        return None
    keys = [
        m.paper_key
        for m in corpus.metadata
        if re.search(rf"\b{re.escape(m.paper_key)}\b", question, re.IGNORECASE)
    ]
    if len(figures) != 1 or len(keys) != 1:
        raise ValueError(
            "Fixed baseline requires one explicit paper and figure in the question"
        )
    return keys[0], f"Figure {figures[0]}"


def build_run(
    case,
    system,
    resources,
    *,
    status,
    answer,
    citations,
    limitations,
    traces,
    observations,
    llm,
    vision,
    latency_ms,
    figure_k,
    extra_errors=None,
):
    items = {
        e.evidence_id: e
        for e in (*resources.corpus.text_evidence, *resources.corpus.figure_evidence)
    }
    acquired = []
    first_text = None
    first_text_status = None
    text_k = None
    events = []
    errors = list(extra_errors or [])
    for trace, observation in zip(traces, observations, strict=True):
        ids = []
        if observation.status == "success":
            payload = observation.payload
            records = [*payload.get("evidence", []), *payload.get("figures", [])]
            if payload.get("figure"):
                records.append(payload["figure"])
            ids = [
                r["evidence_id"]
                for r in records
                if isinstance(r, dict) and r.get("evidence_id") in items
            ]
            acquired.extend(ids)
        if trace.tool_name == "search_text" and first_text is None:
            first_text = ids
            first_text_status = observation.status
            text_k = trace.arguments.get("top_k", 3)
            if type(text_k) is not int or text_k <= 0:
                text_k = None
        event = {
            **trace.model_dump(mode="json"),
            "evidence_ids": ids,
            "observation": observation.model_dump(mode="json"),
        }
        events.append(event)
        if observation.status == "error":
            errors.append(
                {
                    "tool_name": trace.tool_name,
                    "issue": observation.issue.model_dump(),
                    "failure": observation.payload.get("failure", {}),
                }
            )
    acquired = unique(acquired)
    gold_text = [
        i
        for i in case.relevant_evidence_ids
        if not isinstance(items[i], FigureEvidence)
    ]
    gold_figures = [
        i for i in case.relevant_evidence_ids if isinstance(items[i], FigureEvidence)
    ]
    text_ids = [i for i in acquired if not isinstance(items[i], FigureEvidence)]
    figure_ids = [i for i in acquired if isinstance(items[i], FigureEvidence)]
    normalized = [
        EvidenceReference(
            evidence_id=c.evidence_id,
            paper_id=c.paper_id,
            page=c.page,
            figure_id=c.figure_id,
        )
        for c in citations
    ]
    task, source = initial_task_success(case, status, answer)
    return EvaluationRun(
        run_id=uuid4().hex,
        case_id=case.case_id,
        split=case.case_id.split("-")[0],
        category=case.category,
        system=system,
        backend=resources.backend,
        answer=public_value(answer),
        citations=normalized,
        retrieved_evidence_ids=acquired,
        status=status,
        limitations=public_value(limitations),
        **cost_metrics(latency_ms, llm.events, vision.events, traces),
        task_success=task,
        task_success_source=source,
        citation_correctness=None,
        citation_correctness_source="pending_review"
        if normalized
        else "not_applicable",
        citation_identity_valid=citation_identity_valid(
            normalized, acquired, resources.corpus
        ),
        citation_relevance_precision=citation_relevance_precision(
            normalized, case.relevant_evidence_ids
        ),
        text_recall_at_k=recall_at_k(first_text, gold_text, text_k)
        if first_text is not None
        and text_k is not None
        and first_text_status != "error"
        else None,
        text_recall_k=text_k,
        text_evidence_recall=evidence_recall(text_ids, gold_text),
        figure_recall_at_k=recall_at_k(figure_ids, gold_figures, figure_k),
        figure_recall_k=figure_k,
        trace=events,
        llm_events=public_value(llm.events),
        vlm_events=public_value(vision.events),
        errors=public_value(errors),
    )
