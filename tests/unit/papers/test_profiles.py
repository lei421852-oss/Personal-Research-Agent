import json

import pytest
from pydantic import ValidationError

from app.llm.fake import ScriptedLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.papers.models import PaperDocument, PaperProfile
from app.papers.profiles import PaperProfileExtractor, enrich_papers
from app.papers.retriever import PaperRetriever
from app.papers.store import ChromaPaperVectorStore


@pytest.fixture
def paper():
    return PaperDocument(
        paper_id="synthetic-paper",
        title="A source-only multimodal model",
        abstract="A frozen vision encoder is connected to a language model using cross-attention. It supports image captioning and visual grounding.",
        authors=["Test Author"],
        year=2022,
        venue="Test venue",
    )


def output(**changes):
    fields = {
        "tasks": [{"label": "image captioning", "source_quote": "image captioning"}],
        "methods": [{"label": "cross-attention", "source_quote": "cross-attention"}],
        "capabilities": [
            {"label": "visual grounding", "source_quote": "visual grounding"}
        ],
    } | changes
    return LLMResponse(
        tool_calls=[
            ToolCall(id="profile", name="submit_paper_profile", arguments=fields)
        ]
    )


def extract(paper, responses):
    llm = ScriptedLLMClient(responses)
    extractor = PaperProfileExtractor(llm)
    profile = extractor.extract(title=paper.title, abstract=paper.abstract)
    return profile, extractor, llm


def test_structured_extraction_only_receives_title_and_abstract(paper):
    profile, extractor, llm = extract(paper, [output()])
    assert profile.tasks == ["image captioning"]
    assert profile.methods == ["cross-attention"]
    assert profile.capabilities == ["visual grounding"]
    assert extractor.calls == 1 and extractor.events[-1]["status"] == "success"
    user_input = json.loads(llm.calls[0]["messages"][1]["content"])
    assert user_input == {"title": paper.title, "abstract": paper.abstract}
    assert "paper_id" not in user_input and "query" not in user_input
    assert (
        "source_quote"
        in llm.calls[0]["tools"][0]["input_schema"]["$defs"]["SupportedTerm"][
            "properties"
        ]
    )


def test_empty_fields_are_valid_and_not_dependency_failure(paper):
    profile, extractor, _ = extract(
        paper, [output(tasks=[], methods=[], capabilities=[])]
    )
    assert profile == PaperProfile.empty()
    assert extractor.events[-1]["status"] == "empty"


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(content="The model can do everything."),
        LLMResponse(tool_calls=[ToolCall(id="bad", name="wrong", arguments={})]),
        LLMResponse(
            tool_calls=[ToolCall(id="bad", name="submit_paper_profile", arguments={})]
        ),
        output(tasks="captioning"),
        output(tasks=[{"label": 1, "source_quote": "image captioning"}]),
        output(tasks=[{"label": "image captioning", "source_quote": "not in source"}]),
        output(tasks=[{"label": " ", "source_quote": "image captioning"}]),
        output(
            tasks=[
                {"label": str(i), "source_quote": "image captioning"} for i in range(6)
            ]
        ),
        output(extra_field=[]),
    ],
)
def test_invalid_or_ungrounded_output_falls_back_to_empty(paper, response):
    profile, extractor, _ = extract(paper, [response])
    assert profile == PaperProfile.empty()
    assert extractor.events[-1]["status"] == "error"
    assert extractor.events[-1]["issue_code"] == "invalid_profile"


def test_llm_failure_falls_back_without_exposing_exception(paper):
    def fail(messages, tools):
        raise ConnectionError("private provider detail")

    profile, extractor, _ = extract(paper, [fail])
    assert profile == PaperProfile.empty()
    assert extractor.events[-1]["issue_code"] == "dependency_failure"
    assert "private" not in json.dumps(extractor.events)


def test_each_field_deduplicates_case_and_whitespace(paper):
    duplicate = [
        {"label": "image captioning", "source_quote": "image captioning"},
        {"label": " IMAGE   CAPTIONING ", "source_quote": "image captioning"},
    ]
    profile, _, _ = extract(
        paper, [output(tasks=duplicate, methods=duplicate, capabilities=duplicate)]
    )
    assert (
        profile.tasks == profile.methods == profile.capabilities == ["image captioning"]
    )
    document = PaperDocument.model_validate(
        paper.model_dump() | {"tasks": ["a", " A "]}
    )
    assert document.tasks == ["a"]


def test_rich_serialization_and_legacy_document_defaults(paper):
    old = {
        k: v
        for k, v in paper.model_dump().items()
        if k not in {"tasks", "methods", "capabilities"}
    }
    restored = PaperDocument.model_validate(old)
    assert restored.profile == PaperProfile.empty()
    enriched = PaperDocument.model_validate(
        old
        | {
            "tasks": ["image captioning"],
            "methods": ["cross-attention"],
            "capabilities": ["visual grounding"],
        }
    )
    assert enriched.retrieval_text == (
        f"Title: {paper.title}\nAbstract: {paper.abstract}\n"
        "Tasks: image captioning\nMethods: cross-attention\nCapabilities: visual grounding\nKeywords: "
    )
    assert "Test Author" not in enriched.retrieval_text
    assert "synthetic-paper" not in enriched.retrieval_text


def test_document_rejects_overlarge_profile_and_nonstring_terms(paper):
    for values in ({"tasks": [str(i) for i in range(6)]}, {"methods": [1]}):
        with pytest.raises(ValidationError):
            PaperDocument.model_validate(paper.model_dump() | values)


def test_existing_profile_reused_without_llm(paper):
    enriched = PaperDocument.model_validate(
        paper.model_dump() | {"tasks": ["image captioning"]}
    )
    extractor = PaperProfileExtractor(ScriptedLLMClient([]))
    documents, report = enrich_papers([enriched], extractor)
    assert documents == [enriched]
    assert report["llm_calls"] == 0 and report["reused_profile_count"] == 1


@pytest.mark.parametrize(
    "response", [output(), output(tasks=[], methods=[], capabilities=[])]
)
def test_cache_reuses_valid_including_empty_profile(paper, tmp_path, response):
    cache = tmp_path / "profiles.json"
    first = PaperProfileExtractor(ScriptedLLMClient([response]))
    documents, _ = enrich_papers([paper], first, cache_path=cache)
    second = PaperProfileExtractor(ScriptedLLMClient([]))
    reused, report = enrich_papers([paper], second, cache_path=cache)
    assert reused == documents and report["llm_calls"] == 0
    assert report["records"][0]["origin"] == "cache"


def test_cache_invalidates_when_source_changes(paper, tmp_path):
    cache = tmp_path / "profiles.json"
    enrich_papers(
        [paper], PaperProfileExtractor(ScriptedLLMClient([output()])), cache_path=cache
    )
    changed = paper.model_copy(update={"title": "Changed title"})
    _, report = enrich_papers(
        [changed],
        PaperProfileExtractor(ScriptedLLMClient([output()])),
        cache_path=cache,
    )
    assert report["llm_calls"] == 1
    assert report["records"][0]["origin"] == "extracted"


def test_failed_paper_does_not_stop_corpus_and_failure_not_cached(paper, tmp_path):
    second = paper.model_copy(update={"paper_id": "second"})
    cache = tmp_path / "profiles.json"
    extractor = PaperProfileExtractor(
        ScriptedLLMClient([LLMResponse(content="invalid"), output()])
    )
    documents, report = enrich_papers([paper, second], extractor, cache_path=cache)
    assert len(documents) == 2
    assert not documents[0].profile.has_terms and documents[1].profile.has_terms
    assert (
        report["failed_extraction_count"] == 1
        and report["successful_profile_count"] == 1
    )
    saved = json.loads(cache.read_text())
    assert paper.paper_id not in saved["profiles"]


def test_rich_index_roundtrip_and_retriever_contract(paper, tmp_path):
    pytest.importorskip("chromadb")
    enriched = PaperDocument.model_validate(
        paper.model_dump() | {"tasks": ["image captioning"]}
    )
    store = ChromaPaperVectorStore(tmp_path, create=True)
    store.add([enriched], [[1.0, 0.0]])
    raw = store.collection.get(ids=[paper.paper_id], include=["documents", "metadatas"])
    assert raw["documents"] == [enriched.retrieval_text]
    assert (
        PaperDocument.model_validate_json(raw["metadatas"][0]["paper_json"]) == enriched
    )

    class QueryEmbedding:
        def embed_query(self, query):
            assert query == "captioning"
            return [1.0, 0.0]

    hits = PaperRetriever(QueryEmbedding(), store).search("captioning", top_k=10)
    assert hits[0].paper == enriched and hits[0].rank == 1


def test_legacy_index_remains_readable(paper, tmp_path):
    pytest.importorskip("chromadb")
    store = ChromaPaperVectorStore(tmp_path, create=True)
    old = {
        k: v
        for k, v in paper.model_dump().items()
        if k not in {"tasks", "methods", "capabilities"}
    }
    store.collection.upsert(
        ids=[paper.paper_id],
        embeddings=[[1.0, 0.0]],
        documents=[paper.legacy_retrieval_text],
        metadatas=[
            {
                "record_type": "paper",
                "paper_id": paper.paper_id,
                "paper_json": json.dumps(old),
            }
        ],
    )
    hit = store.search([1.0, 0.0], top_k=1)[0]
    assert hit.paper == paper and not hit.paper.profile.has_terms
