"""工作台只读历史和论文详情契约；不连接数据库或模型。"""

from fastapi.testclient import TestClient

from app.workbench import create_app
from tests.test_research_run_manager import FakeDependencies, make_manager


class FakeRepository:
    def __init__(self):
        self.calls = []

    async def list_run_history(self, *, limit, offset):
        self.calls.append((limit, offset))
        return (
            [
                {
                    "run_id": "a" * 32,
                    "question": "测试问题",
                    "status": "completed",
                    "mode": "live",
                    "created_at": "2026-01-01T00:00:00Z",
                    "report_available": True,
                }
            ],
            21,
        )


class FakeLifecycle:
    def __init__(self):
        self.repository = FakeRepository()

    async def close(self):
        pass


def test_server_history_pagination_and_input_validation():
    manager = make_manager(dependencies=FakeDependencies(False))
    manager.lifecycle = FakeLifecycle()
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        response = client.get("/api/research/runs", params={"page": 2, "page_size": 10})
        assert response.status_code == 200
        assert response.json()["total"] == 21
        assert response.json()["items"][0]["question"] == "测试问题"
        assert manager.lifecycle.repository.calls == [(10, 10)]
        assert client.get("/api/research/runs?page=0").status_code == 422
        assert client.get("/api/research/runs?page_size=101").status_code == 422


def test_history_unavailable_is_not_an_empty_success():
    manager = make_manager(dependencies=FakeDependencies(False))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        response = client.get("/api/research/runs")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "history_unavailable"


def test_catalog_paper_detail_and_missing_id():
    manager = make_manager(dependencies=FakeDependencies(False))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        catalog = client.get("/api/research/papers").json()["catalog"]
        assert catalog
        paper_id = catalog[0]["paper_id"]
        response = client.get("/api/research/papers/" + paper_id)
        assert response.status_code == 200
        assert response.json()["paper_id"] == paper_id
        assert response.json()["abstract"]
        assert response.json()["abstract_kind"] == "source_based_summary"
        assert client.get("/api/research/papers/unknown").status_code == 404


def test_catalog_remains_visible_when_prepared_corpus_is_unavailable():
    class BrokenDependencies(FakeDependencies):
        def papers(self):
            raise RuntimeError("unavailable")

    manager = make_manager(dependencies=BrokenDependencies(False))
    with TestClient(create_app(manager_factory=lambda: manager)) as client:
        response = client.get("/api/research/papers")
        assert response.status_code == 200
        assert response.json()["availability"] == "unavailable"
        catalog = response.json()["catalog"]
        assert catalog and all(item["availability"] == "unknown" for item in catalog)
        detail = client.get("/api/research/papers/" + catalog[0]["paper_id"])
        assert detail.status_code == 200
        assert detail.json()["availability"] == "unknown"
