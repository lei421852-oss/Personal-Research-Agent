"""进入 Reviewer 前核验结构、真实引用、观察账本和单论文归属。"""

from app.evidence.models import FigureEvidence, TextEvidence
from app.evidence.resolver import EvidenceResolutionError
from app.research.models import EvidenceRequirement, RequirementStatus, WorkerResult
from app.research.review_models import (
    ReviewEvidence,
    ReviewIssue,
    ReviewResult,
    ReviewStatus,
    ReviewTask,
    ReviewValidationStatus,
)
from app.tools.redaction import public_value


def _nonempty_attr(instance, name, fallback):
    value = getattr(instance, name, None)
    if not isinstance(value, str) or not value.strip():
        return fallback
    safe = public_value(value)
    return safe.strip() or fallback


def invalid_review_result(worker, requirement, reason="invalid_structure"):
    """坏的 model_construct 样本也要返回合法、脱敏的拒绝结果。"""
    claim = getattr(requirement, "claim", None)
    claim = public_value(claim) if isinstance(claim, str) and claim.strip() else None
    identities = getattr(requirement, "evidence_ids", [])
    identities = (
        list(
            dict.fromkeys(
                public_value(i).strip()
                for i in identities
                if isinstance(i, str) and i.strip()
            )
        )
        if isinstance(identities, list)
        else []
    )
    return ReviewResult(
        worker_task_id=_nonempty_attr(worker, "worker_task_id", "invalid_worker"),
        requirement_id=_nonempty_attr(
            requirement, "requirement_id", "invalid_requirement"
        ),
        paper_id=_nonempty_attr(requirement, "paper_id", "invalid_paper"),
        status=ReviewStatus.NOT_REVIEWABLE,
        reason_code=reason,
        claim=claim,
        evidence_ids=identities,
        validation_status=ReviewValidationStatus.REJECTED,
        issue=ReviewIssue(
            code=reason,
            message=(
                "Cited evidence does not exist in the current corpus."
                if reason == "evidence_missing"
                else "Worker requirement structure, observed scope or paper identity is invalid."
            ),
        ),
    )


class DeterministicReviewValidator:
    def __init__(self, resolver):
        self.resolver = resolver

    def validate(
        self, worker: WorkerResult, requirement: EvidenceRequirement
    ) -> ReviewTask | ReviewResult:
        try:
            # 不直接 model_validate(existing_model)：Pydantic 默认可能信任已构建实例。
            # 先转为普通结构，确保 model_construct 和被篡改的嵌套实例重新校验。
            checked_worker = WorkerResult.model_validate(
                worker.model_dump(mode="python", warnings=False)
            )
            checked = EvidenceRequirement.model_validate(
                requirement.model_dump(mode="python", warnings=False)
            )
            matching = [
                r
                for r in checked_worker.requirements
                if r.requirement_id == checked.requirement_id
            ]
            if (
                len(matching) != 1
                or matching[0].model_dump() != checked.model_dump()
                or checked.paper_id != checked_worker.paper_id
            ):
                return invalid_review_result(worker, requirement)
        except (AttributeError, TypeError, ValueError):
            return invalid_review_result(worker, requirement)

        if checked.status != RequirementStatus.SUPPORTED:
            # insuff 是合法业务结果；系统失败/取消保留原语义，不伪造语义审核结论。
            return ReviewResult(
                worker_task_id=checked_worker.worker_task_id,
                requirement_id=checked.requirement_id,
                paper_id=checked.paper_id,
                status=ReviewStatus.NOT_REVIEWABLE,
                validation_status=(
                    ReviewValidationStatus.PASSED
                    if checked.status == RequirementStatus.INSUFFICIENT_EVIDENCE
                    else ReviewValidationStatus.SKIPPED
                ),
                skip_reason=checked.status.value,
            )

        evidence = []
        for identity in checked.evidence_ids:
            try:
                item = self.resolver.resolve(identity)
            except (EvidenceResolutionError, KeyError):
                return invalid_review_result(worker, requirement, "evidence_missing")
            try:
                if not isinstance(item, (TextEvidence, FigureEvidence)):
                    return invalid_review_result(worker, requirement)
                item = type(item).model_validate(
                    item.model_dump(mode="python", warnings=False)
                )
                if (
                    item.evidence_id != identity
                    or item.paper_id != checked.paper_id
                    or identity not in checked_worker.observed_evidence_ids
                ):
                    return invalid_review_result(worker, requirement)
                # 仅显式投影这些字段；asset_path 永远不能进入 Reviewer 输入。
                evidence.append(
                    ReviewEvidence(
                        evidence_id=item.evidence_id,
                        paper_id=item.paper_id,
                        page=item.page,
                        modality=item.modality,
                        text=item.text,
                        caption=item.caption
                        if isinstance(item, FigureEvidence)
                        else None,
                        figure_id=item.figure_id
                        if isinstance(item, FigureEvidence)
                        else None,
                    )
                )
            except (AttributeError, TypeError, ValueError):
                return invalid_review_result(worker, requirement)
        return ReviewTask(
            worker_task_id=checked_worker.worker_task_id,
            requirement=checked,
            evidence=evidence,
        )
