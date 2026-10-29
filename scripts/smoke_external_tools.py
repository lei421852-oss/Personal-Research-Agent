"""本地stdio契约演示；只调用仓库内模拟服务，不使用Provider、arXiv或数据库。"""

import argparse
import json
import sys
import threading
from pathlib import Path

from app.tools.executor import ToolExecutor
from app.tools.integrations.adapter import LocalStdioSource
from app.tools.integrations.models import ExternalServiceConfig
from app.tools.registry import ToolRegistry

SCENARIOS = {
    "success": ("normal", "success", None),
    "empty": ("empty", "empty", "empty_result"),
    "invalid_input": ("normal", "error", "invalid_arguments"),
    "unavailable": ("unavailable", "error", "dependency_failure"),
    "timeout": ("sleep", "error", "dependency_failure"),
    "cancelled": ("normal", "error", "internal_failure"),
    "output_limit": ("oversize", "error", "internal_failure"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/p1-local-tool-smoke")
    )
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    args = parser.parse_args()
    mock = (
        Path(__file__).resolve().parents[1]
        / "tests/unit/tools/integrations/mock_service.py"
    )
    results = []
    for scenario in SCENARIOS if args.scenario == "all" else [args.scenario]:
        mode, expected, issue = SCENARIOS[scenario]
        config = ExternalServiceConfig(
            provider="fixture",
            enabled=True,
            allowed_tools=("echo",),
            timeout_seconds=0.5,
            max_output_bytes=4096,
        )
        cancel = threading.Event()
        session = LocalStdioSource(config, (sys.executable, str(mock), mode)).open(
            cancel_event=cancel
        )
        try:
            registry = ToolRegistry()
            session.register(registry)
            if scenario == "cancelled":
                cancel.set()
            arguments = {"query": "local-fixture-only"}
            if scenario == "invalid_input":
                arguments["unexpected"] = True
            executor = ToolExecutor(registry)
            observation = executor.execute("external__fixture__echo", arguments)
            assert observation.status == expected
            assert (observation.issue.code if observation.issue else None) == issue
            results.append(
                {
                    "scenario": scenario,
                    "observation": observation.model_dump(mode="json"),
                    "trace": [item.model_dump(mode="json") for item in executor.traces],
                    "transport_calls": list(session.client.records),
                }
            )
        finally:
            session.close()
            assert not session.client.active_pids
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "observations.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "kind": "local_stdio_simulation_not_production_acceptance",
                "real_arxiv_requests": 0,
                "paid_llm_calls": 0,
                "database_connections": 0,
                "cases": results,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
    print(
        json.dumps(
            {"status": "passed", "case_count": len(results), "output": str(output)}
        )
    )


if __name__ == "__main__":
    main()
