import json
from pathlib import Path

import pytest

from app.evidence.corpus import load_paper_corpus
from app.evidence.errors import EvidenceDependencyError
from app.evidence.models import RetrievedEvidence
from app.evidence.repository import FigureAssetResolver, PaperEvidenceRepository
from app.retrieval.errors import RetrievalDependencyError
from app.tools.executor import ToolExecutor
from app.tools.paper import FindFigureTool, ReadPageTool, SearchTextTool
from app.tools.paper_registry import create_paper_registry
from app.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ["search_text", "find_figure", "read_page"]
LEAK = "Traceback (most recent call last): /home/lei/project/private sk-test-secret-123456 API key"


@pytest.fixture
def corpus():
    return load_paper_corpus(ROOT / "data/papers")


class FakeRetrieval:
    def __init__(self, corpus, *, empty=False, failure=None):
        self.corpus, self.empty, self.failure = corpus, empty, failure
        self.calls = []

    def search_text(self, query, paper_ids, top_k):
        self.calls.append((query, paper_ids, top_k))
        if self.failure is not None:
            raise self.failure
        if self.empty:
            return []
        items = [
            e
            for e in self.corpus.text_evidence
            if paper_ids is None or e.paper_id in paper_ids
        ]
        return [
            RetrievedEvidence(evidence=e, retrieval_score=0.75) for e in items[:top_k]
        ]


def arguments(tool):
    return {
        "search_text": {
            "query": "Perceiver Resampler",
            "paper_ids": ["flamingo"],
            "top_k": 1,
        },
        "find_figure": {"paper_id": "blip", "figure_id": "Figure 3"},
        "read_page": {"paper_id": "flamingo", "page": 11},
    }[tool]


@pytest.mark.parametrize("tool", TOOLS)
def test_normal_result_and_execution_trace(corpus, tool):
    executor = ToolExecutor(create_paper_registry(corpus, FakeRetrieval(corpus)))
    observation = executor.execute(tool, arguments(tool), tool_call_id="call-1")
    assert observation.status == "success"
    assert observation.issue is None
    assert observation.result_count >= 1
    trace = executor.traces[0]
    assert trace.tool_call_id == "call-1"
    assert trace.tool_name == tool
    assert trace.arguments == arguments(tool)
    assert trace.status == "success"
    assert trace.latency_ms >= 0
    assert trace.result_count == observation.result_count
    combined = observation.model_dump_json() + trace.model_dump_json()
    assert "/home/" not in combined and "asset_path" not in combined
    assert "Traceback" not in combined and "sk-test-secret" not in combined
    assert set(trace.model_dump()) == {
        "tool_call_id",
        "tool_name",
        "arguments",
        "status",
        "latency_ms",
        "result_count",
        "failure",
    }
    assert trace.failure is None


@pytest.mark.parametrize("tool", TOOLS)
def test_empty_is_not_a_failure(corpus, tool):
    executor = ToolExecutor(
        create_paper_registry(corpus, FakeRetrieval(corpus, empty=True))
    )
    args = arguments(tool)
    if tool == "find_figure":
        args["figure_id"] = "Figure 999"
    if tool == "read_page":
        args["page"] = 35
    result = executor.execute(tool, args)
    assert result.status == "empty"
    assert result.issue.code == "empty_result"
    assert result.issue.retryable is False
    assert result.result_count == executor.traces[0].result_count == 0
    if tool == "read_page":
        assert result.payload["reason"] == "page_not_prepared"
        assert result.payload["prepared_pages"] == [4, 9, 10, 11]


@pytest.mark.parametrize("tool", TOOLS)
def test_invalid_arguments_are_nonretryable_and_safe(corpus, tool, monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "private-api-credential-987654")
    executor = ToolExecutor(create_paper_registry(corpus, FakeRetrieval(corpus)))
    result = executor.execute(
        tool, {"unexpected": LEAK, "api_key": "private-api-credential-987654"}
    )
    assert result.status == "error"
    assert result.issue.code == "invalid_arguments"
    assert result.issue.retryable is False
    combined = result.model_dump_json() + executor.traces[0].model_dump_json()
    for forbidden in [
        "/home/",
        "Traceback",
        "sk-test-secret",
        "private-api-credential-987654",
    ]:
        assert forbidden not in combined


@pytest.mark.parametrize("tool", TOOLS)
def test_unknown_paper_is_not_empty_and_does_not_retry(corpus, tool):
    retrieval = FakeRetrieval(corpus)
    executor = ToolExecutor(create_paper_registry(corpus, retrieval))
    args = arguments(tool)
    if tool == "search_text":
        args["paper_ids"] = ["clip", "unknown-paper"]
    else:
        args["paper_id"] = "unknown-paper"
    result = executor.execute(tool, args)
    assert result.status == "error"
    assert result.issue.code == "unknown_paper"
    assert result.issue.retryable is False
    assert not retrieval.calls


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "failure,code,retryable",
    [
        (EvidenceDependencyError(LEAK), "dependency_failure", True),
        (RuntimeError(LEAK), "internal_failure", False),
    ],
)
def test_dependency_and_internal_failures_are_classified_without_leaks(
    corpus, tool, failure, code, retryable
):
    class FailingRepository(PaperEvidenceRepository):
        def find_figure(self, paper_id, figure_id):
            raise failure

        def read_page(self, paper_id, page):
            raise failure

    papers = FailingRepository(corpus)
    registry = ToolRegistry()
    registry.register(SearchTextTool(FakeRetrieval(corpus, failure=failure), papers))
    registry.register(FindFigureTool(papers))
    registry.register(ReadPageTool(papers))
    executor = ToolExecutor(registry)
    result = executor.execute(tool, arguments(tool))
    assert result.status == "error"
    assert result.issue.code == code
    assert result.issue.retryable is retryable
    assert result.result_count == executor.traces[0].result_count == 0
    output = result.model_dump_json() + executor.traces[0].model_dump_json()
    for forbidden in ["/home/", "Traceback", "sk-test-secret", "Chroma"]:
        assert forbidden not in output


@pytest.mark.parametrize(
    "papers", [["clip"], ["blip"], ["flamingo"], ["clip", "blip"], None, []]
)
def test_search_paper_scope_and_public_evidence_fields(corpus, papers):
    retrieval = FakeRetrieval(corpus)
    executor = ToolExecutor(create_paper_registry(corpus, retrieval))
    result = executor.execute(
        "search_text",
        {"query": "BLIP noisy captions", "paper_ids": papers, "top_k": 30},
    )
    resolved = None if papers is None else [corpus.paper_aliases[p] for p in papers]
    assert retrieval.calls[0][1] == resolved
    allowed = set(corpus.paper_aliases.values()) if resolved is None else set(resolved)
    for item in result.payload["evidence"]:
        assert item["paper_id"] in allowed
        assert {
            "evidence_id",
            "paper_id",
            "page",
            "text",
            "retrieval_score",
        } <= item.keys()
    assert result.status == ("empty" if papers == [] else "success")


def test_search_rejects_out_of_scope_or_stale_evidence(corpus):
    class LeakingRetrieval(FakeRetrieval):
        def search_text(self, query, paper_ids, top_k):
            return [
                RetrievedEvidence(
                    evidence=self.corpus.text_evidence[0], retrieval_score=0.9
                )
            ]

    result = ToolExecutor(
        create_paper_registry(corpus, LeakingRetrieval(corpus))
    ).execute("search_text", {"query": "BLIP", "paper_ids": ["clip"], "top_k": 1})
    assert result.status == "error" and result.issue.code == "internal_failure"
    assert result.payload["failure"]["message"] == "Retrieval violated paper scope"
    assert "evidence" not in result.payload


def test_figure_exact_lookup_and_private_asset_resolution(corpus):
    executor = ToolExecutor(create_paper_registry(corpus, FakeRetrieval(corpus)))
    found = executor.execute(
        "find_figure", {"paper_id": "blip", "figure_id": "Figure 3"}
    )
    figure = found.payload["figure"]
    assert figure["paper_id"] == corpus.paper_aliases["blip"]
    assert figure["page"] == 4 and figure["figure_id"] == "Figure 3"
    resolver = FigureAssetResolver(corpus)
    assert resolver.resolve(figure["asset_ref"]).is_file()
    with pytest.raises(KeyError):
        resolver.resolve("/home/lei/project/private")
    near = executor.execute(
        "find_figure", {"paper_id": "blip", "figure_id": "Figure 30"}
    )
    assert near.status == "empty"


def test_read_page_reports_prepared_region_boundary_and_actual_motivation(corpus):
    executor = ToolExecutor(create_paper_registry(corpus, FakeRetrieval(corpus)))
    found = executor.execute("read_page", {"paper_id": "flamingo", "page": 11})
    context = " ".join(item["text"] for item in found.payload["evidence"])
    assert "64" in context and "computational complexity" in context
    assert found.payload["coverage"] == "prepared_regions_only"
    assert all(item["page"] == 11 for item in found.payload["evidence"])
    assert (
        executor.execute("read_page", {"paper_id": "flamingo", "page": 35}).status
        == "empty"
    )


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_text", {"query": " ", "top_k": 1}),
        ("search_text", {"query": "x", "top_k": 0}),
        ("search_text", {"query": "x", "top_k": True}),
        ("search_text", {"query": "x", "paper_ids": [""]}),
        ("find_figure", {"paper_id": "blip", "figure_id": " "}),
        ("read_page", {"paper_id": "flamingo", "page": 0}),
        ("read_page", {"paper_id": "flamingo", "page": "11"}),
        ("read_page", {"paper_id": "flamingo", "page": True}),
    ],
)
def test_strict_argument_contracts(corpus, tool, args):
    result = ToolExecutor(create_paper_registry(corpus, FakeRetrieval(corpus))).execute(
        tool, args
    )
    assert result.issue.code == "invalid_arguments"


def test_unknown_tool_is_safe_and_traced(corpus):
    executor = ToolExecutor(create_paper_registry(corpus, FakeRetrieval(corpus)))
    result = executor.execute(LEAK, {})
    assert result.issue.code == "unknown_tool"
    assert (
        "/home/" not in result.model_dump_json() + executor.traces[0].model_dump_json()
    )


def test_registry_has_three_model_callable_schemas(corpus):
    schemas = create_paper_registry(corpus, FakeRetrieval(corpus)).schemas()
    assert {s["name"] for s in schemas} == set(TOOLS)
    assert all(s["input_schema"]["additionalProperties"] is False for s in schemas)
    assert "asset_path" not in json.dumps(schemas)


def test_chroma_dependency_error_is_retryable(corpus):
    retrieval = FakeRetrieval(corpus, failure=RetrievalDependencyError(LEAK))
    result = ToolExecutor(create_paper_registry(corpus, retrieval)).execute(
        "search_text", arguments("search_text")
    )
    assert result.issue.code == "dependency_failure" and result.issue.retryable
