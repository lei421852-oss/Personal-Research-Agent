"""中文工作台语言边界：只改变可读文字，原问题、证据与身份保持可追溯。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.llm.fake import FakeLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.research.query_language import (
    QueryLanguageAdapter,
    contains_chinese,
    query_terms,
)
from app.research.report_language import (
    ReportLanguageAdapter,
    load_replay_translation,
    readable_report_fields,
    report_source_sha256,
)
from app.research.run_presenter import present_execution
from app.research.run_replay import ReplayRepository

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "比较 BLIP-2 和 Flamingo 的 Q-Former 与 Perceiver Resampler"
ENGLISH = "Compare BLIP-2 and Flamingo, focusing on Q-Former and Perceiver Resampler."


def response(name, arguments):
    return LLMResponse(
        tool_calls=[ToolCall(id="language-call", name=name, arguments=arguments)]
    )


def query_response(english=ENGLISH, terms=None):
    return response(
        "translate_research_question",
        {
            "english_question": english,
            "preserved_terms": query_terms(QUESTION) if terms is None else terms,
        },
    )


def report(case_id="g2-1"):
    return present_execution(ReplayRepository(ROOT).load(case_id))


def fixture(case_id="g2-1"):
    return json.loads(
        (ROOT / "data/workbench_translations" / f"{case_id}.json").read_text()
    )


def test_chinese_and_mixed_query_require_adaptation():
    assert contains_chinese("比较连接模块")
    assert contains_chinese(QUESTION)
    assert not contains_chinese("Compare CLIP and BLIP")
    client = FakeLLMClient(query_response())
    result = QueryLanguageAdapter(client).adapt(QUESTION)
    assert result.status == "adapted"
    assert result.original_question == QUESTION
    assert result.research_question == ENGLISH
    assert result.terms == ["BLIP-2", "Flamingo", "Q-Former", "Perceiver", "Resampler"]
    assert result.model_calls == len(client.calls) == 1
    assert result.latency_ms >= 0


def test_all_chinese_query_can_translate_without_supplied_latin_terms():
    question = "比较视觉语言连接机制，不要引用语料库之外的资料。"
    client = FakeLLMClient(
        response(
            "translate_research_question",
            {
                "english_question": "Compare visual-language connection mechanisms using only the corpus.",
                "preserved_terms": [],
            },
        )
    )
    result = QueryLanguageAdapter(client).adapt(question)
    assert result.status == "adapted"
    assert result.original_question == question


@pytest.mark.parametrize(
    "question,mode",
    [("Explain CLIP contrastive training.", "auto"), (QUESTION, "original")],
)
def test_english_and_original_mode_do_not_call_provider(question, mode):
    client = FakeLLMClient(query_response())
    result = QueryLanguageAdapter(client).adapt(question, mode)
    assert result.status == "passthrough"
    assert result.research_question == result.original_question == question
    assert result.model_calls == 0
    assert client.calls == []


def test_prepare_and_parse_can_use_outer_resource_gate_without_internal_call():
    client = FakeLLMClient(query_response())
    adapter = QueryLanguageAdapter(client)
    messages, tools = adapter.prepare(QUESTION)
    assert json.loads(messages[-1]["content"])["original_question"] == QUESTION
    assert tools[0]["input_schema"]["additionalProperties"] is False
    assert adapter.parse_response(QUESTION, query_response()).status == "adapted"
    assert client.calls == []


@pytest.mark.parametrize(
    "bad",
    [
        query_response(english="Compare BLIP-2 and Flamingo."),
        query_response(english=""),
        query_response(english="比较 BLIP-2 Flamingo Q-Former Perceiver Resampler"),
        query_response(terms=["BLIP-2"]),
        response("other", {}),
        LLMResponse(content="unstructured English answer"),
        LLMResponse(
            tool_calls=[query_response().tool_calls[0], query_response().tool_calls[0]]
        ),
        response(
            "translate_research_question",
            {
                "english_question": ENGLISH,
                "preserved_terms": query_terms(QUESTION),
                "answer": "invented",
            },
        ),
    ],
)
def test_invalid_query_translation_never_submits_an_altered_question(bad):
    result = QueryLanguageAdapter(FakeLLMClient(bad)).adapt(QUESTION)
    assert result.status == "failed"
    assert result.research_question is None
    assert result.original_question == QUESTION
    assert result.issue_code == "invalid_query_translation"
    assert result.model_calls == 1


def test_query_missing_provider_is_explained_without_call():
    result = QueryLanguageAdapter().adapt(QUESTION)
    assert result.status == "failed"
    assert result.model_calls == 0
    assert result.research_question is None


def test_query_provider_failure_is_bounded_and_sanitized():
    class Broken:
        def __init__(self):
            self.count = 0

        def complete(self, **kwargs):
            self.count += 1
            raise RuntimeError("sk-secret-api-key /home/private Traceback")

    client = Broken()
    result = QueryLanguageAdapter(client).adapt(QUESTION)
    assert result.status == "failed"
    assert result.model_calls == client.count == 1
    assert "sk-secret" not in result.model_dump_json()
    assert "/home/private" not in result.model_dump_json()


@pytest.mark.parametrize(
    "question,mode",
    [("", "auto"), (" " * 4, "auto"), ("x" * 4001, "auto"), (QUESTION, "unknown")],
)
def test_query_argument_validation(question, mode):
    with pytest.raises(ValueError, match="invalid_query_language_arguments"):
        QueryLanguageAdapter().prepare(question, mode)


@pytest.mark.parametrize("case_id,claims,missing", [("g2", 5, 7), ("g2-1", 4, 10)])
def test_historical_chinese_display_preserves_authority_and_never_calls_provider(
    case_id, claims, missing
):
    original = report(case_id)
    snapshot = deepcopy(original)
    result = load_replay_translation(case_id, original, ROOT)
    assert result.status == "translated"
    assert result.model_calls == 0
    assert result.translation_source == "offline"
    assert result.translation_review == "not_independently_reviewed"
    assert result.source_sha256 == report_source_sha256(original)
    assert original == snapshot
    translated = result.display_report
    assert translated["title"] == "已审核发现与证据缺口"
    assert translated["status"] == original["status"]
    assert len(translated["verified_claims"]) == claims
    assert len(translated["missing_requirements"]) == missing
    assert translated["missing_requirements"] == original["missing_requirements"]
    assert translated["metrics"] == original["metrics"]
    assert translated["human_review"] == original["human_review"]
    assert translated["provenance"] == original["provenance"]
    for new, old in zip(
        translated["verified_claims"], original["verified_claims"], strict=True
    ):
        assert new["claim_id"] == old["claim_id"]
        assert new["requirement_id"] == old["requirement_id"]
        assert new["paper_id"] == old["paper_id"]
        assert new["dimension_id"] == old["dimension_id"]
        assert new["review_status"] == "passed"
        assert new["evidence"] == old["evidence"]
        assert all(e["evidence_id"] in translated["markdown"] for e in old["evidence"])
    assert "独立人工事实审核" in translated["markdown"]
    assert "未形成已核验的跨论文比较" in translated["markdown"]


def test_report_translation_one_call_using_existing_tool_schema():
    original = report()
    client = FakeLLMClient(
        response("translate_report_fields", {"translations": fixture()["translations"]})
    )
    result = ReportLanguageAdapter(client).adapt(original)
    assert result.status == "translated"
    assert result.model_calls == len(client.calls) == 1
    requested = json.loads(client.calls[0]["messages"][-1]["content"])["fields"]
    assert requested == readable_report_fields(original)
    assert all("/evidence/" not in key for key in requested)
    assert "sk-" not in result.model_dump_json()


def test_report_prepare_parse_is_pure_and_accepts_outer_call_budget():
    original = report()
    adapter = ReportLanguageAdapter()
    messages, tools = adapter.prepare(original)
    assert set(json.loads(messages[-1]["content"])["fields"]) == set(
        fixture()["translations"]
    )
    schema = tools[0]["input_schema"]["properties"]["translations"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(readable_report_fields(original))
    result = adapter.parse_response(
        original,
        response(
            "translate_report_fields", {"translations": fixture()["translations"]}
        ),
    )
    assert result.status == "translated"
    assert result.model_calls == 1


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "extra",
        "empty",
        "wrong_type",
        "numeric",
        "new_citation",
        "english",
        "secret",
    ],
)
def test_invalid_report_translation_falls_back_without_changing_original(defect):
    original = report()
    snapshot = deepcopy(original)
    translations = fixture()["translations"]
    if defect == "missing":
        translations.pop("/title")
    if defect == "extra":
        translations["/verified_claims/0/evidence/0/page"] = "100"
    if defect == "empty":
        translations["/title"] = " "
    if defect == "wrong_type":
        translations["/title"] = ["标题"]
    if defect == "numeric":
        translations["/verified_claims/0/text"] = translations[
            "/verified_claims/0/text"
        ].replace("32", "64", 1)
    if defect == "new_citation":
        translations["/verified_claims/0/text"] += "参考 ev_fake。"
    if defect == "english":
        translations["/title"] = "Verified findings"
    if defect == "secret":
        translations["/title"] = "标题 sk-abcdefghijklmnopqrstuvwxyz123456"
    result = ReportLanguageAdapter().apply_translations(original, translations)
    assert result.status == "failed"
    assert result.display_report == original == snapshot
    assert result.issue_code == "invalid_report_translation"


def test_citation_marker_present_in_translatable_text_must_survive():
    original = {
        "status": "completed",
        "title": "Finding ev_original",
        "question": "Explain CLIP",
        "verified_claims": [],
        "limitations": [],
    }
    translations = {"/title": "发现 ev_original", "/question": "解释 CLIP"}
    result = ReportLanguageAdapter().apply_translations(original, translations)
    assert result.status == "translated"
    translations["/title"] = "发现 ev_replacement"
    assert (
        ReportLanguageAdapter().apply_translations(original, translations).status
        == "failed"
    )


@pytest.mark.parametrize(
    "bad",
    [
        LLMResponse(content="free report"),
        response("wrong_action", {}),
        response(
            "translate_report_fields",
            {"translations": fixture()["translations"], "report": "new"},
        ),
    ],
)
def test_report_response_requires_exact_structured_action(bad):
    original = report()
    result = ReportLanguageAdapter().parse_response(original, bad)
    assert result.status == "failed"
    assert result.display_report == original


def test_report_failure_has_one_physical_call_and_preserves_successful_research():
    class BudgetedBroken:
        def __init__(self):
            self.used = 0

        def complete(self, **kwargs):
            assert self.used == 0
            self.used += 1
            raise RuntimeError("private-provider-error")

    original = report()
    client = BudgetedBroken()
    result = ReportLanguageAdapter(client).adapt(original)
    assert result.status == "failed"
    assert result.display_report["status"] == original["status"] == "completed"
    assert client.used == result.model_calls == 1
    assert "private-provider-error" not in result.model_dump_json()


def test_unavailable_report_needs_no_translation_call():
    client = FakeLLMClient(LLMResponse())
    original = {
        "status": "report_unavailable",
        "title": "",
        "question": "",
        "markdown": "",
    }
    result = ReportLanguageAdapter(client).adapt(original)
    assert result.status == "not_needed"
    assert result.display_report == original
    assert client.calls == []


@pytest.mark.parametrize("mutation", ["text", "status", "evidence", "metrics"])
def test_historical_translation_refuses_source_mismatch(mutation):
    original = report()
    if mutation == "text":
        original["verified_claims"][0]["text"] += " modified"
    if mutation == "status":
        original["missing_requirements"][0]["status"] = "completed"
    if mutation == "evidence":
        original["verified_claims"][0]["evidence"][0]["page"] += 1
    if mutation == "metrics":
        original["metrics"]["model_calls"] += 1
    result = load_replay_translation("g2-1", original, ROOT)
    assert result.status == "failed"
    assert result.issue_code == "replay_translation_source_mismatch"
    assert result.model_calls == 0
    assert result.display_report == original


def test_missing_or_unknown_historical_fixture_falls_back_with_zero_calls(tmp_path):
    original = report()
    assert (
        load_replay_translation("g2-1", original, tmp_path).issue_code
        == "replay_translation_unavailable"
    )
    result = load_replay_translation("arbitrary-path", original, ROOT)
    assert result.issue_code == "unknown_replay_translation"
    assert result.model_calls == 0


def test_query_numeric_constraint_cannot_be_silently_changed():
    question = "比较 BLIP-2 和 Flamingo，只输出3段。"
    client = FakeLLMClient(
        response(
            "translate_research_question",
            {
                "english_question": "Compare BLIP-2 and Flamingo in 4 paragraphs.",
                "preserved_terms": query_terms(question),
            },
        )
    )
    result = QueryLanguageAdapter(client).adapt(question)
    assert result.status == "failed"
    assert result.research_question is None
