"""中文工作台 API 的零选择、语言副本与脱敏边界。"""

import asyncio
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.research.report_language import readable_report_fields, report_source_sha256
from app.research.run_manager import ResearchRunManager, ResearchRunRequest
from app.research.run_replay import ReplayRepository
from app.research.workbench_dependencies import ROOT, WorkbenchDependencies
from app.workbench import create_app
from tests.test_chinese_workbench_runtime import FakeDependencies, fake_runtime


def test_request_optional_scope_and_single_scope_explicitly_rejected():
    assert ResearchRunRequest(question="比较论文连接方式").paper_ids == []
    assert ResearchRunRequest(question="比较", paper_ids=[]).language_mode == "auto"
    with pytest.raises(ValidationError):
        ResearchRunRequest(question="比较", paper_ids=["clip-2103.00020v1"])


def test_zero_scope_request_runs_complete_default_chain_and_report_has_original():
    async def scenario():
        runtime, dependency, _ = fake_runtime()
        manager = ResearchRunManager(runtime, dependency, ReplayRepository(ROOT))
        run = await manager.create(
            ResearchRunRequest(question="比较 BLIP-2 与 Flamingo 的连接方式。")
        )
        await manager._runs[run["run_id"]].task
        status = manager.status(run["run_id"])
        original = manager.report(run["run_id"])
        chinese = original["chinese_report"]
        assert status["status"] == "completed"
        assert status["paper_selection"]["status"] == "selected"
        assert status["query_language"]["status"] == "adapted"
        assert original["language"]["status"] == "translated"
        assert (
            original["language"]["translation_review"] == "not_independently_reviewed"
        )
        assert original["markdown"] != chinese["markdown"]
        assert original["metrics"] == chinese["metrics"]
        for original_claim, translated_claim in zip(
            original["verified_claims"], chinese["verified_claims"], strict=True
        ):
            for field in original_claim:
                if field != "text":
                    assert original_claim[field] == translated_claim[field]
        assert original["missing_requirements"] == chinese["missing_requirements"]
        assert original["comparisons"] == chinese["comparisons"]
        authoritative = {
            key: value
            for key, value in original.items()
            if key not in {"language", "chinese_report"}
        }
        restored = deepcopy(chinese)
        for pointer, text in readable_report_fields(authoritative).items():
            parts = pointer.lstrip("/").split("/")
            current = restored
            for part in parts[:-1]:
                current = (
                    current[int(part)] if isinstance(current, list) else current[part]
                )
            if isinstance(current, list):
                current[int(parts[-1])] = text
            else:
                current[parts[-1]] = text
        restored["markdown"] = authoritative["markdown"]
        assert restored == authoritative
        assert original["language"]["final_source_sha256"] == report_source_sha256(
            authoritative
        )
        await manager.close()

    asyncio.run(scenario())


def test_http_zero_selection_provider_missing_is_safe_and_single_selection_is_422():
    dependency = FakeDependencies()
    dependency.health = lambda: {
        "real_research_ready": False,
        "language_adapter_ready": False,
    }
    runtime, _, _ = fake_runtime()
    manager = ResearchRunManager(runtime, dependency, ReplayRepository(ROOT))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        missing = client.post(
            "/api/research/runs", json={"question": "比较 BLIP-2 与 Flamingo。"}
        )
        assert missing.status_code == 503
        assert "Traceback" not in missing.text
        single = client.post(
            "/api/research/runs",
            json={"question": "研究 CLIP。", "paper_ids": ["clip-2103.00020v1"]},
        )
        assert single.status_code == 422
        malicious = client.post(
            "/api/research/runs",
            json={
                "question": "",
                "api_key": "sk-private-never-echo",
                "path": "/home/private",
            },
        )
        assert malicious.status_code == 422
        assert "sk-private" not in malicious.text and "/home/" not in malicious.text


def test_replay_chinese_is_offline_preserves_original_status_and_never_calls_runtime():
    runtime, dependency, created = fake_runtime()
    dependency.health = lambda: {
        "ui_ready": True,
        "real_research_ready": False,
        "language_adapter_ready": False,
        "replay_ready": True,
    }
    manager = ResearchRunManager(runtime, dependency, ReplayRepository(ROOT))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        for case_id in ("g2", "g2-1"):
            run = client.post(f"/api/research/replays/{case_id}").json()
            report = client.get(f"/api/research/runs/{run['run_id']}/report").json()
            assert report["language"]["model_calls"] == 0
            assert report["language"]["translation_source"] == "offline"
            assert report["language"]["source"] == "offline_replay"
            assert report["chinese_report"]["status"] == report["status"]
            assert (
                report["chinese_report"]["missing_requirements"]
                == report["missing_requirements"]
            )
        assert created == []


def test_paper_library_distinguishes_catalog_only_from_prepared():
    runtime, dependency, _ = fake_runtime()
    manager = ResearchRunManager(runtime, dependency, ReplayRepository(ROOT))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        library = client.get("/api/research/papers").json()
        assert len(library["papers"]) == 5
        assert len(library["catalog"]) == 25
        ofa = next(
            paper
            for paper in library["catalog"]
            if paper["paper_id"] == "ofa-2202.03052v1"
        )
        assert ofa["availability"] == "metadata_only"
        assert ofa["paper_id"] not in {paper["paper_id"] for paper in library["papers"]}


def test_language_readiness_is_independent_of_missing_corpus(tmp_path, monkeypatch):
    dependency = WorkbenchDependencies(root=tmp_path)
    monkeypatch.setattr(
        dependency,
        "environment",
        lambda: {
            "LLM_API_KEY": "fake",
            "LLM_MODEL": "fake",
            "LLM_BASE_URL": "https://fake.invalid",
        },
    )
    health = dependency.health()
    assert health["language_adapter_ready"] is True
    assert health["real_research_ready"] is False
    assert "/" + "tmp" not in json.dumps(health)
