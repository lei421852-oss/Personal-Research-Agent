"""历史 CLIP 答案的离线转换契约，不调用真实模型。"""

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from app.research.report_language import ReportLanguageAdapter
from app.research.retry import DependencyCallError
from app.workbench import create_app
from tests.test_research_run_manager import FakeDependencies, make_manager


def saved_report():
    path = Path(__file__).parent / "fixtures/clip_report_translation.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    report = {
        "status": "completed",
        "direct_answer": record["direct_answer"],
        "citations": [
            {"paper_id": "clip-2103.00020v1", "page": 4, "evidence_id": "ev_original"}
        ],
        "verified_claims": [],
        "limitations": [],
    }
    return report, {"/direct_answer": record["translation"]}


def test_saved_clip_answer_can_be_translated_without_changing_evidence():
    report, translations = saved_report()
    snapshot = deepcopy(report)
    result = ReportLanguageAdapter().apply_translations(report, translations)
    assert result.status == "translated"
    assert "对比学习" in result.display_report["direct_answer"]
    assert result.display_report["citations"] == snapshot["citations"]
    assert report == snapshot


def test_missing_translation_field_has_safe_diagnosis_and_original_fallback():
    report, _ = saved_report()
    snapshot = deepcopy(report)
    result = ReportLanguageAdapter().apply_translations(report, {})
    assert result.issue_code == "invalid_report_translation"
    assert result.issue_detail == "missing_fields"
    assert result.display_report == report == snapshot


def test_invalid_json_is_not_misclassified_as_provider_outage():
    class BadJsonClient:
        def complete(self, **kwargs):
            return json.loads("{")

    report, _ = saved_report()
    snapshot = deepcopy(report)
    result = ReportLanguageAdapter(BadJsonClient()).adapt(report)
    assert result.issue_code == "invalid_report_translation"
    assert result.issue_detail == "invalid_json"
    assert result.display_report == report == snapshot
    wrapped = DependencyCallError(failure={"error_type": "JSONDecodeError"})
    assert ReportLanguageAdapter.exception_issue_detail(wrapped) == "invalid_json"


def test_normal_workbench_denies_new_runs_server_side_by_default():
    manager = make_manager(dependencies=FakeDependencies(False))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        response = client.post("/api/research/runs", json={"question": "test"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "research_entry_disabled"
        assert manager._runs == {}
        assert client.get("/").status_code == 200
        replay = client.post(
            "/api/research/runs",
            headers={"Idempotency-Replay-Only": "true", "Idempotency-Key": "test"},
            json={"question": "test"},
        )
        assert replay.status_code == 404
        assert manager._runs == {}


def test_numeric_change_is_rejected_with_specific_reason():
    report, translations = saved_report()
    translations["/direct_answer"] = translations["/direct_answer"].replace("4×", "5×")
    result = ReportLanguageAdapter().apply_translations(report, translations)
    assert result.issue_detail == "numeric_mismatch"
    assert result.display_report == report


def test_invalid_json_survives_async_provider_boundary():
    import asyncio

    from app.research.async_tools import AsyncLLMAdapter
    from app.research.runtime import ResourceGate

    class BadJsonClient:
        def complete(self, **kwargs):
            return json.loads("{")

    async def run():
        adapter = AsyncLLMAdapter(BadJsonClient(), ResourceGate())
        try:
            await adapter.complete(messages=[], tools=[], attempt_budget=1)
        except DependencyCallError as exc:
            assert ReportLanguageAdapter.exception_issue_detail(exc) == "invalid_json"
            assert exc.attempts == 1
        else:
            raise AssertionError("invalid JSON was accepted")
        finally:
            await adapter.drain()

    asyncio.run(run())
