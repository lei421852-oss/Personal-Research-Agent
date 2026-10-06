"""固定一条 dev 题验证三个模拟系统，不加载真实模型适配器。"""

import json

from eval.loader import load_dataset
from eval.runners.fixed_multimodal_rag import FixedMultimodalRAGRunner
from eval.runners.multimodal_agent import MultimodalAgentRunner
from eval.runners.resources import create_resources
from eval.runners.text_rag import TextRAGRunner
from eval.schemas import EvaluationRun


def run_smoke() -> list[EvaluationRun]:
    case = next(
        c
        for c in load_dataset("dev", require_approved=True).cases
        if c.case_id == "dev-text-01"
    )
    resources = create_resources("fake")
    runs = []
    for runner in (TextRAGRunner, FixedMultimodalRAGRunner, MultimodalAgentRunner):
        result = runner(resources).run(case)
        # 验证公开结果能正确序列化和重新校验，不只检查 Python 返回类型。
        validated = EvaluationRun.model_validate_json(result.model_dump_json())
        if validated.backend != "fake" or validated.status != "completed":
            raise RuntimeError(f"Smoke failed: {validated.system}")
        runs.append(validated)
    return runs


def main() -> None:
    print(
        json.dumps(
            {
                "scope": "1 dev case x 3 fake systems; no real model evaluation",
                "runs": [run.model_dump(mode="json") for run in run_smoke()],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
