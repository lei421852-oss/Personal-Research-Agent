"""HTTP到后台任务与真实历史产物映射；全程无付费Provider。"""

from fastapi.testclient import TestClient

from app.workbench import create_app
from tests.test_research_run_manager import FakeDependencies, make_manager


def test_workbench_http_replay_and_incremental_events():
    manager = make_manager(dependencies=FakeDependencies(False))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/workbench-static/app.js").status_code == 200
        assert client.get("/api/research/health").json()["real_research_ready"] is False
        assert len(client.get("/api/research/papers").json()["papers"]) == 2
        assert len(client.get("/api/research/replays").json()["cases"]) == 2
        result = client.post("/api/research/replays/g2").json()
        assert result["mode"] == "replay"
        identity = result["run_id"]
        events = client.get(f"/api/research/runs/{identity}/events").json()
        assert events["events"]
        assert (
            client.get(
                f"/api/research/runs/{identity}/events",
                params={"after": events["next_cursor"]},
            ).json()["events"]
            == []
        )
        report = client.get(f"/api/research/runs/{identity}/report").json()
        assert len(report["verified_claims"]) == 5
        assert len(report["missing_requirements"]) == 7
        assert (
            client.post(f"/api/research/runs/{identity}/cancel").json()["status"]
            == "completed"
        )
        assert client.get("/api/research/runs/unknown").status_code == 404
        assert client.post("/api/research/replays/not-a-path").status_code == 404


def test_http_validation_and_unavailable_provider_have_safe_errors():
    manager = make_manager(dependencies=FakeDependencies(False))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        bad = client.post(
            "/api/research/runs",
            json={
                "question": "",
                "paper_ids": ["a", "b"],
                "api_key": "sk-secret-that-must-not-return",
            },
        )
        assert bad.status_code == 422
        assert "secret" not in bad.text
        invalid = client.post(
            "/api/research/runs", json={"question": "question", "paper_ids": ["a", "b"]}
        )
        assert invalid.status_code == 422
        missing = client.post(
            "/api/research/runs",
            json={
                "question": "question",
                "paper_ids": ["blip2-2301.12597v1", "flamingo-2204.14198v1"],
            },
        )
        assert missing.status_code == 503
        assert missing.json()["error"]["code"] == "research_unavailable"
        assert (
            client.get(
                "/api/research/runs/unknown/events", params={"after": -1}
            ).status_code
            == 422
        )


def test_legal_http_run_duplicate_and_cancel_use_background_manager():
    manager = make_manager()
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        payload = {
            "question": "Compare prepared architectures",
            "paper_ids": ["blip2-2301.12597v1", "flamingo-2204.14198v1"],
        }
        created = client.post("/api/research/runs", json=payload)
        assert created.status_code == 202
        identity = created.json()["run_id"]
        assert (
            client.get(f"/api/research/runs/{identity}").json()["status"] == "running"
        )
        assert client.get(f"/api/research/runs/{identity}/report").status_code == 409
        assert client.post("/api/research/runs", json=payload).status_code == 409
        result = client.post(f"/api/research/runs/{identity}/cancel")
        assert result.status_code == 200
        assert result.json()["cancel_requested"] is True
        assert (
            client.get(f"/api/research/runs/{identity}").json()["status"] == "cancelled"
        )
