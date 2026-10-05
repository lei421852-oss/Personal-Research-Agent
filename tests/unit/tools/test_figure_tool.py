import json
from pathlib import Path

import pytest

from app.evidence.citations import CitationValidationError, CitationValidator
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.qa.service import MultimodalQAService
from app.tools.errors import DependencyFailure
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from app.vision.client import VLMResponse

ROOT = Path(__file__).resolve().parents[3]


class FakeVLM:
    def __init__(self, mode="ok"):
        self.mode, self.calls = mode, []

    def analyze(self, image_path, prompt):
        self.calls.append((image_path, prompt))
        if self.mode == "failure":
            raise DependencyFailure("secret /home/private/key sk-not-public")
        figure_id = prompt.split("Evidence ID: ")[1].splitlines()[0]
        used = (
            [figure_id]
            if self.mode == "ok"
            else (["ev_fake"] if self.mode == "fake" else [])
        )
        return VLMResponse(
            text=json.dumps(
                {
                    "observations": ["Visible pipeline."],
                    "interpretation": "A diagram does not prove superior performance.",
                    "evidence_used": used,
                    "limitations": ["No performance measurements."],
                }
            )
        )


class NoRetrieval:
    def search_text(self, *args):
        raise AssertionError("Fixed workflow must not search dynamically")


@pytest.fixture
def setup():
    corpus = load_paper_corpus(ROOT / "data/papers")
    figure = next(e for e in corpus.figure_evidence if e.paper_id.startswith("blip"))
    contexts = [
        e
        for e in corpus.text_evidence
        if e.paper_id == figure.paper_id and e.page == figure.page
    ]
    return corpus, figure, contexts


def build(corpus, mode="ok"):
    client = FakeVLM(mode)
    executor = ToolExecutor(
        create_paper_registry(corpus, NoRetrieval(), analyzer=FigureAnalyzer(client))
    )
    return executor, client


def args(figure, contexts):
    return {
        "question": "Verify premise",
        "figure_evidence_id": figure.evidence_id,
        "context_evidence_ids": [e.evidence_id for e in contexts],
    }


def test_success_and_prompt(setup):
    corpus, figure, contexts = setup
    executor, client = build(corpus)
    result = executor.execute("analyze_figure", args(figure, contexts))
    assert result.status == "success"
    assert result.result_count == 1 + len(contexts)
    path, prompt = client.calls[0]
    assert path == figure.asset_path
    assert figure.caption in prompt
    for e in contexts:
        assert e.evidence_id in prompt and e.text in prompt
    assert "/home/" not in result.model_dump_json()
    assert "observations" in result.payload["analysis"]
    assert "interpretation" in result.payload["analysis"]


@pytest.mark.parametrize(
    "mode,code",
    [
        ("fake", "citation_validation_failure"),
        ("empty", "citation_validation_failure"),
        ("failure", "dependency_failure"),
    ],
)
def test_output_failures(setup, mode, code):
    corpus, figure, contexts = setup
    executor, _ = build(corpus, mode)
    result = executor.execute("analyze_figure", args(figure, contexts))
    assert result.status == "error" and result.issue.code == code
    assert result.issue.retryable == (mode == "failure")
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "wrong",
        "foreign_context",
        "figure_context",
        "other_paper",
        "path",
        "invalid",
    ],
)
def test_invalid_evidence_and_arguments(setup, case):
    corpus, figure, contexts = setup
    executor, client = build(corpus)
    inputs = args(figure, contexts)
    if case == "unknown":
        inputs["figure_evidence_id"] = "ev_unknown"
    if case == "wrong":
        inputs["figure_evidence_id"] = contexts[0].evidence_id
    if case == "foreign_context":
        inputs["context_evidence_ids"] = ["ev_outside"]
    if case == "figure_context":
        inputs["context_evidence_ids"] = [figure.evidence_id]
    if case == "other_paper":
        inputs["context_evidence_ids"] = [
            next(
                e.evidence_id
                for e in corpus.text_evidence
                if e.paper_id != figure.paper_id
            )
        ]
    if case == "path":
        inputs["asset_path"] = "/tmp/anything.png"
    if case == "invalid":
        inputs["question"] = " "
    result = executor.execute("analyze_figure", inputs)
    assert result.status == "error"
    assert result.issue.code == (
        "invalid_arguments" if case in {"path", "invalid"} else "invalid_evidence"
    )
    assert client.calls == []


def test_citation_subset():
    validator = CitationValidator()
    assert validator.validate(["A", "B", "A"], ["A", "B", "C"]) == ("A", "B")
    for used in [["A", "FAKE"], []]:
        with pytest.raises(CitationValidationError):
            validator.validate(used, ["A", "B", "C"])


def test_workflow_success_and_per_request_trace(setup):
    corpus, _, _ = setup
    executor, client = build(corpus)
    service = MultimodalQAService(executor, EvidenceResolver(corpus))
    for _ in range(2):
        result = service.answer("Verify premise", "blip", "Figure 3")
        assert result.status == "success" and result.citations_validated
        assert result.tool_calls == 3
        assert [t.tool_name for t in result.trace] == [
            "find_figure",
            "read_page",
            "analyze_figure",
        ]
        assert result.citations[0].page == 4
        assert result.citations[0].figure_id == "Figure 3"
        assert result.total_latency_ms >= sum(t.latency_ms for t in result.trace)
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "paper,figure,page,count",
    [
        ("unknown", "Figure 3", None, 1),
        ("blip", "missing", None, 1),
        ("blip", "Figure 3", 35, 2),
    ],
)
def test_workflow_stops(setup, paper, figure, page, count):
    corpus, _, _ = setup
    executor, client = build(corpus)
    result = MultimodalQAService(executor, EvidenceResolver(corpus)).answer(
        "Explain", paper, figure, context_page=page
    )
    assert (
        result.status != "success" and result.answer is None and result.citations == []
    )
    assert result.tool_calls == count and client.calls == []


def test_workflow_vlm_failure(setup):
    corpus, _, _ = setup
    executor, _ = build(corpus, "failure")
    result = MultimodalQAService(executor, EvidenceResolver(corpus)).answer(
        "Explain", "blip", "Figure 3"
    )
    assert result.status == "error" and result.tool_calls == 3
    assert result.answer is None and not result.citations_validated


def test_find_dependency_failure_stops_workflow(setup, monkeypatch):
    corpus, _, _ = setup
    executor, client = build(corpus)

    def fail(_):
        raise DependencyFailure("backend unavailable")

    monkeypatch.setattr(executor.registry.get("find_figure"), "execute", fail)
    result = MultimodalQAService(executor, EvidenceResolver(corpus)).answer(
        "Explain", "blip", "Figure 3"
    )
    assert result.status == "error" and result.tool_calls == 1
    assert result.observations[0].issue.code == "dependency_failure"
    assert client.calls == []


def test_read_dependency_failure_stops_workflow(setup, monkeypatch):
    corpus, _, _ = setup
    executor, client = build(corpus)

    def fail(_):
        raise DependencyFailure("backend unavailable")

    monkeypatch.setattr(executor.registry.get("read_page"), "execute", fail)
    result = MultimodalQAService(executor, EvidenceResolver(corpus)).answer(
        "Explain", "blip", "Figure 3"
    )
    assert result.status == "error" and result.tool_calls == 2
    assert client.calls == []


def test_provider_exception_is_dependency_failure(setup):
    from types import SimpleNamespace

    from app.vision.provider import OpenAICompatibleVisionClient

    _, figure, _ = setup

    def fail(**kwargs):
        raise RuntimeError("API key /home/private/path")

    sdk = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fail))
    )
    provider = OpenAICompatibleVisionClient(
        api_key="fake", model="fake", base_url="https://unused", sdk_client=sdk
    )
    with pytest.raises(DependencyFailure):
        provider.analyze(figure.asset_path, "Explain")


@pytest.mark.parametrize("mode", ["fake", "empty"])
def test_workflow_rejects_invalid_citations(setup, mode):
    corpus, _, _ = setup
    executor, _ = build(corpus, mode)
    result = MultimodalQAService(executor, EvidenceResolver(corpus)).answer(
        "Explain", "blip", "Figure 3"
    )
    assert result.status == "error" and result.tool_calls == 3
    assert result.observations[-1].issue.code == "citation_validation_failure"
    assert result.answer is None and result.citations == []
