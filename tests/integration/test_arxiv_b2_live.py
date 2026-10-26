"""B2独立授权的最终提交真实工具测试；不继承B1门控。"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.papers.arxiv import ArxivClient
from app.papers.catalog import load_paper_catalog
from app.tools.arxiv import SearchArxivTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(
    os.environ.get("B2_TEST_ARXIV_AUTHORIZED") != "yes",
    reason="B2 requires its own explicit B2_TEST_ARXIV_AUTHORIZED=yes",
)


@pytest.fixture(scope="module")
def live():
    client = ArxivClient()
    observations = []
    registry = ToolRegistry()
    registry.register(SearchArxivTool(client, load_paper_catalog()))

    class RecordingExecutor(ToolExecutor):
        def execute(self, *args, **kwargs):
            result = super().execute(*args, **kwargs)
            observations.append(result.model_dump(mode="json"))
            return result

    executor = RecordingExecutor(registry)
    yield executor
    assert not client._transport.active_pids
    assert all(r["reaped"] for r in client._transport.cleanup_records)
    target = os.environ.get("B2_TEST_ARXIV_OUTPUT_DIR")
    if target:
        with (Path(target) / "b2-real-tool-observations.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(
                {
                    "kind": "B2_real_tool_contract",
                    "code_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "observations": observations,
                    "trace": [t.model_dump(mode="json") for t in executor.traces],
                    "cleanup_records": client._transport.cleanup_records,
                    "real_model_calls": 0,
                    "postgresql_connections": 0,
                },
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.write("\n")


def test_b2_final_code_real_success(live):
    result = live.execute(
        "search_arxiv",
        {
            "query": 'ti:"Learning Transferable Visual Models From Natural Language Supervision"',
            "top_k": 1,
        },
    )
    assert result.status == "success", result.model_dump_json()
    assert result.payload["papers"][0]["arxiv_id"] == "2103.00020v1"
    assert 1 <= result.payload["execution"]["attempts"] <= 2


def test_b2_final_code_real_empty(live):
    result = live.execute(
        "search_arxiv", {"query": 'all:"codexb1nomatch8b7a4ff2e2bf40cc9b8"', "top_k": 1}
    )
    assert result.status == "empty" and result.issue.code == "empty_result", (
        result.model_dump_json()
    )
    assert result.payload["execution"]["attempts"] == 1
