"""显式运行三条真实 Planner smoke；不执行 Worker 或任何检索。"""

import argparse
import json
import os
from pathlib import Path

from app.papers.catalog import load_paper_catalog
from app.research.planner import ResearchPlanner, ResearchPlannerError

ROOT = Path(__file__).resolve().parents[1]
BLIP2 = "blip-2-2301.12597v1"
FLAMINGO = "flamingo-2204.14198v1"
LLAVA = "llava-2304.08485v1"
SMOKE_CASES = [
    {
        "case_id": "planner-smoke-01",
        "question": "Compare BLIP-2, Flamingo, and LLaVA in terms of their architecture, visual-language connection, training strategy, trainable parameters, and downstream capabilities.",
        "paper_ids": [BLIP2, FLAMINGO, LLAVA],
    },
    {
        "case_id": "planner-smoke-02",
        "question": "What are the major differences between BLIP-2, Flamingo, and LLaVA, and why do they adopt different designs?",
        "paper_ids": [BLIP2, FLAMINGO, LLAVA],
    },
    {
        "case_id": "planner-smoke-03",
        "question": "Compare the training cost and parameter efficiency of BLIP-2 and Flamingo.",
        "paper_ids": [BLIP2, FLAMINGO],
    },
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/research-planner-smoke.json"
    )
    args = parser.parse_args()
    from dotenv import load_dotenv

    from app.llm.provider import OpenAICompatibleLLMClient

    if not args.env_file.is_file():
        parser.error("Configured environment file does not exist")
    load_dotenv(args.env_file, override=False)
    if any(
        not os.environ.get(f"LLM_{key}") for key in ("API_KEY", "MODEL", "BASE_URL")
    ):
        parser.error("Planner smoke requires LLM credentials")
    llm = OpenAICompatibleLLMClient(
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
        base_url=os.environ["LLM_BASE_URL"],
    )
    planner = ResearchPlanner(llm, load_paper_catalog())
    report = {
        "scope": "Three planner-only smoke tests; no Worker, retrieval or QA evaluation.",
        "cases": [],
        "status": "running",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for case in SMOKE_CASES:
            row = dict(case)
            try:
                result = planner.prepare(case["question"], case["paper_ids"])
                row.update(
                    status="success",
                    dimensions=[
                        d.model_dump(mode="json") for d in result.plan.dimensions
                    ],
                    requirement_count=len(result.requirements),
                    worker_task_count=len(result.worker_tasks),
                    worker_tasks=[
                        {
                            "worker_task_id": w.worker_task_id,
                            "paper_id": w.paper_id,
                            "requirement_ids": [
                                r.requirement_id for r in w.requirements
                            ],
                        }
                        for w in result.worker_tasks
                    ],
                    requirements=[
                        r.model_dump(mode="json") for r in result.requirements
                    ],
                    trace=result.trace.model_dump(mode="json"),
                    semantic_review={
                        "status": "pending_manual_review",
                        "answer_assumption": None,
                        "scope_drift": None,
                    },
                )
            except ResearchPlannerError as exc:
                row.update(
                    status="error",
                    error_code=exc.code,
                    error_message=str(exc),
                    trace=planner.traces[-1].model_dump(mode="json"),
                )
            report["cases"].append(row)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(
                case["case_id"],
                row["status"],
                row["trace"]["dimension_count"],
                row["trace"]["model_calls"],
                flush=True,
            )
    finally:
        llm.close()
    report["status"] = (
        "completed"
        if all(c["status"] == "success" for c in report["cases"])
        else "completed_with_errors"
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
