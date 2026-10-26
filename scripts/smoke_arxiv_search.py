"""通过正式Registry/Executor调用一个公开论文工具；不启动研究或Provider。"""

import argparse
import json
import os
from pathlib import Path

from app.papers.arxiv import ArxivClient
from app.papers.catalog import load_paper_catalog
from app.tools.arxiv import SearchArxivTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.environ.get("B2_TEST_ARXIV_AUTHORIZED") != "yes":
        parser.error(
            "Set B2_TEST_ARXIV_AUTHORIZED=yes to authorize one real metadata tool call (at most two HTTP attempts)."
        )
    registry = ToolRegistry()
    registry.register(SearchArxivTool(ArxivClient(), load_paper_catalog()))
    executor = ToolExecutor(registry)
    observation = executor.execute(
        "search_arxiv", {"query": args.query, "top_k": args.top_k}
    )
    result = {
        "observation": observation.model_dump(mode="json"),
        "trace": [t.model_dump(mode="json") for t in executor.traces],
        "real_llm_calls": 0,
        "database_connections": 0,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text, end="")
    return 0 if observation.status in {"success", "empty"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
