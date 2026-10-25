"""显式授权门控的真实工具契约测试；不调用任何LLM或数据库。"""

import json
import os
from pathlib import Path

import pytest

from app.papers.arxiv import ArxivClient
from app.papers.catalog import load_paper_catalog
from app.tools.arxiv import SearchArxivTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(
    os.environ.get("B1_TEST_ARXIV_AUTHORIZED") != "yes",
    reason="Real arXiv tool calls require B1_TEST_ARXIV_AUTHORIZED=yes",
)


@pytest.fixture(scope="module")
def live_executor():
    registry = ToolRegistry()
    registry.register(SearchArxivTool(ArxivClient(), load_paper_catalog()))
    observations = []

    class RecordingExecutor(ToolExecutor):
        def execute(self, *args, **kwargs):
            observation = super().execute(*args, **kwargs)
            observations.append(observation.model_dump(mode="json"))
            return observation

    executor = RecordingExecutor(registry)
    yield executor
    target = os.environ.get("B1_TEST_ARXIV_OUTPUT_DIR")
    if target:
        destination = Path(target) / "real-tool-observations.json"
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "kind": "real_registered_tool_contract",
                    "observations": observations,
                    "trace": [t.model_dump(mode="json") for t in executor.traces],
                    "model_provider_calls": 0,
                    "database_connections": 0,
                },
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.write("\n")


def test_real_registered_tool_success(live_executor):
    observation = live_executor.execute(
        "search_arxiv",
        {
            "query": 'ti:"Learning Transferable Visual Models From Natural Language Supervision"',
            "top_k": 1,
        },
    )
    assert observation.status == "success", observation.model_dump_json()
    assert observation.payload["papers"][0]["arxiv_id"] == "2103.00020v1"
    assert (
        observation.payload["papers"][0]["source_url"]
        == "https://arxiv.org/abs/2103.00020v1"
    )
    assert not observation.payload["papers"][0]["paper_evidence_available"]


def test_real_registered_tool_empty(live_executor):
    observation = live_executor.execute(
        "search_arxiv", {"query": 'all:"codexb1nomatch8b7a4ff2e2bf40cc9b8"', "top_k": 1}
    )
    assert observation.status == "empty", observation.model_dump_json()
    assert (
        observation.issue.code == "empty_result" and observation.payload["papers"] == []
    )
