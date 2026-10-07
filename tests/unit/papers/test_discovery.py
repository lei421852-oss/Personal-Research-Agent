import json
from pathlib import Path

import pytest

from app.agent.loop import BoundedToolAgent
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.llm.fake import ScriptedLLMClient
from app.papers.catalog import load_paper_catalog
from app.papers.models import RetrievedPaper
from app.papers.retriever import PaperRetriever
from app.retrieval.errors import RetrievalDependencyError
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from scripts.demo_day5 import ControlledRetrieval, action, last_observation

ROOT = Path(__file__).resolve().parents[3]
CLIP = "clip-2103.00020v1"
BLIP = "blip-2201.12086v2"
BLIP2 = "blip-2-2301.12597v1"


@pytest.fixture
def catalog():
    return load_paper_catalog()


@pytest.fixture
def corpus():
    return load_paper_corpus(ROOT / "data/papers")


class FakeEmbedding:
    def __init__(self):
        self.queries = []

    def embed_query(self, query):
        self.queries.append(query)
        return [1.0, 0.0]


class FixedPaperStore:
    def __init__(self, papers):
        self.hits = [
            RetrievedPaper(paper=p, score=1.0 - i * 0.1, rank=i + 1)
            for i, p in enumerate(papers)
        ]
        self.calls = []

    def search(self, vector, *, top_k):
        self.calls.append(top_k)
        return self.hits[:top_k]


def executor(corpus, catalog, hits):
    retrieval = ControlledRetrieval(corpus)
    paper_retriever = PaperRetriever(FakeEmbedding(), FixedPaperStore(hits))
    registry = create_paper_registry(
        corpus, retrieval, paper_retriever=paper_retriever, paper_catalog=catalog
    )
    return ToolExecutor(registry), paper_retriever


def test_search_papers_contract_success_empty_and_invalid(corpus, catalog):
    ex, retriever = executor(
        corpus, catalog, [catalog.resolve(CLIP), catalog.resolve(BLIP2)]
    )
    obs = ex.execute("search_papers", {"query": "models", "top_k": 2})
    assert obs.status == "success" and obs.result_count == 2
    first, second = obs.payload["papers"]
    assert (
        first["paper_evidence_available"] is True
        and second["paper_evidence_available"] is False
    )
    assert set(first) == {
        "paper_id",
        "title",
        "year",
        "venue",
        "score",
        "abstract_snippet",
        "paper_discovered",
        "paper_evidence_available",
    }
    assert len(second["abstract_snippet"]) <= 280
    assert (
        "evidence_id" not in obs.model_dump_json()
        and "/home/" not in obs.model_dump_json()
    )
    for args in (
        {"query": ""},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": True},
    ):
        obs = ex.execute("search_papers", args)
        assert obs.status == "error" and obs.issue.code == "invalid_arguments"
    empty, _ = executor(corpus, catalog, [])
    obs = empty.execute("search_papers", {"query": "x"})
    assert obs.status == "empty" and obs.issue.code == "empty_result"

    def fail(*args):
        raise RetrievalDependencyError("not public")

    retriever.search = fail
    obs = ex.execute("search_papers", {"query": "x"})
    assert obs.status == "error" and obs.issue.code == "dependency_failure"


def test_discovered_paper_without_evidence_and_partial_scope(corpus, catalog):
    ex, _ = executor(corpus, catalog, [])
    obs = ex.execute("search_text", {"query": "query", "paper_ids": [BLIP2]})
    assert obs.status == "empty" and obs.issue.code == "empty_result"
    assert obs.payload["availability_status"] == "unavailable"
    assert (
        obs.payload["paper_discovered"] and not obs.payload["paper_evidence_available"]
    )
    assert obs.payload["evidence"] == []
    partial = ex.execute("search_text", {"query": "query", "paper_ids": [BLIP2, CLIP]})
    assert (
        partial.status == "success"
        and partial.payload["availability_status"] == "partial"
    )
    assert partial.payload["unavailable_paper_ids"] == [BLIP2]
    assert all(e["paper_id"] == CLIP for e in partial.payload["evidence"])
    unknown = ex.execute("search_text", {"query": "query", "paper_ids": ["fake-paper"]})
    assert unknown.status == "error" and unknown.issue.code == "unknown_paper"


@pytest.mark.parametrize("ids", [[CLIP], [CLIP, BLIP]])
def test_named_papers_direct_search_without_discovery(corpus, catalog, ids):
    ex, paper_retriever = executor(corpus, catalog, [catalog.resolve(BLIP2)])

    def finish(messages, tools):
        items = last_observation(messages)["payload"]["evidence"]
        return action(
            "finish",
            "finish",
            answer="Scripted answer",
            evidence_ids=[items[0]["evidence_id"]],
            limitations=[],
        )

    llm = ScriptedLLMClient(
        [action("search", "search_text", query="objectives", paper_ids=ids), finish]
    )
    result = BoundedToolAgent(
        llm, ex, EvidenceResolver(corpus, paper_catalog=catalog)
    ).run("Compare CLIP and BLIP" if len(ids) > 1 else "CLIP objective")
    assert result.status == "completed" and result.tool_calls == 1
    assert paper_retriever.vector_store.calls == []
    assert [t.tool_name for t in result.state.trace] == ["search_text"]


def test_open_discovery_then_scoped_retrieval_and_no_metadata_citation(corpus, catalog):
    ex, _ = executor(corpus, catalog, [catalog.resolve(BLIP2), catalog.resolve(CLIP)])

    def scope(messages, tools):
        obs = last_observation(messages)
        assert obs["tool_name"] == "search_papers"
        return action(
            "text",
            "search_text",
            query="objective",
            paper_ids=[obs["payload"]["papers"][0]["paper_id"]],
        )

    def invalid_finish(messages, tools):
        assert (
            last_observation(messages)["payload"]["availability_status"]
            == "unavailable"
        )
        return action(
            "invalid",
            "finish",
            answer="Unsupported claim",
            evidence_ids=[BLIP2],
            limitations=[],
        )

    def safe_finish(messages, tools):
        assert last_observation(messages)["issue"]["code"] == "invalid_arguments"
        return action(
            "finish",
            "finish",
            answer="Prepared evidence unavailable",
            evidence_ids=[],
            limitations=["Discovered paper has no prepared evidence."],
        )

    llm = ScriptedLLMClient(
        [
            action("discover", "search_papers", query="open research"),
            scope,
            invalid_finish,
            safe_finish,
        ]
    )
    result = BoundedToolAgent(
        llm, ex, EvidenceResolver(corpus, paper_catalog=catalog)
    ).run("Find research on frozen visual language models")
    assert result.status == "insufficient_evidence" and not result.citations
    assert not result.state.observed_evidence_ids
    assert [t.tool_name for t in result.state.trace] == ["search_papers", "search_text"]
    ctx = json.loads(
        llm.calls[1]["messages"][0]["content"].split(
            "Current decision context (data):\n"
        )[1]
    )
    assert ctx["discovered_papers"][0]["paper_id"] == BLIP2


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_page", {"paper_id": BLIP2, "page": 1}),
        ("find_figure", {"paper_id": BLIP2, "figure_id": "Figure 1"}),
    ],
)
def test_discovered_paper_page_and_figure_are_unavailable(corpus, catalog, name, args):
    ex, _ = executor(corpus, catalog, [])
    observation = ex.execute(name, args)
    assert observation.status == "empty"
    assert observation.issue.code == "empty_result"
    assert observation.payload["availability_status"] == "unavailable"
    assert observation.result_count == 0
