"""确定性展开与统计；这里不调用 LLM、工具或检索器。"""

import hashlib
import json

from app.research.models import (
    EvidenceRequirement,
    RequirementStatus,
    ResearchCompletion,
    ResearchPlan,
    WorkerTask,
)


def stable_id(prefix: str, *parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(payload.encode()).hexdigest()[:32]


def expand_requirements(plan: ResearchPlan) -> list[EvidenceRequirement]:
    """Paper × Dimension，每对身份在同一规划范围内可稳定重建。"""
    return [
        EvidenceRequirement(
            requirement_id=stable_id("req_", paper_id, dimension.dimension_id),
            paper_id=paper_id,
            dimension_id=dimension.dimension_id,
            question=dimension.research_question,
        )
        for paper_id in plan.paper_ids
        for dimension in plan.dimensions
    ]


def group_worker_tasks(requirements: list[EvidenceRequirement]) -> list[WorkerTask]:
    """保持计划顺序，每篇论文一个待执行任务，不创建 Worker Agent。"""
    ids = [r.requirement_id for r in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate requirement_id")
    grouped = {}
    for requirement in requirements:
        grouped.setdefault(requirement.paper_id, []).append(requirement)
    return [
        WorkerTask(
            worker_task_id=stable_id("worker_", paper_id),
            paper_id=paper_id,
            requirements=items,
        )
        for paper_id, items in grouped.items()
    ]


def research_completion(requirements: list[EvidenceRequirement]) -> ResearchCompletion:
    """证据不足是已解决结果；执行失败和取消仍不满足研究完成条件。"""
    ids = [r.requirement_id for r in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate requirement_id")
    total = len(requirements)
    supported = sum(r.status == RequirementStatus.SUPPORTED for r in requirements)
    resolved = sum(
        r.status
        in (RequirementStatus.SUPPORTED, RequirementStatus.INSUFFICIENT_EVIDENCE)
        for r in requirements
    )
    return ResearchCompletion(
        total_requirements=total,
        resolved_requirements=resolved,
        supported_requirements=supported,
        resolution_rate=resolved / total if total else 0,
        coverage=supported / total if total else 0,
        research_complete=bool(total) and resolved == total,
    )
