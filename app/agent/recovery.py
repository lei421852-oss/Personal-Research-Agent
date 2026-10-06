"""比较动作并提供可操作的恢复反馈；不自动选择工具或修改论文范围。"""

import re
import unicodedata
from dataclasses import dataclass

from pydantic import ValidationError

from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.paper import SearchTextInput

SEARCH_JACCARD_THRESHOLD = 0.85
QUERY_POLARITY_TOKENS = frozenset({"not", "no", "without", "versus", "vs"})


@dataclass(frozen=True)
class SearchActionFingerprint:
    """查询、论文范围和 K 构成搜索指纹，词元集合用于近重复比较。"""

    query: str
    tokens: frozenset[str]
    paper_ids: tuple[str, ...] | None
    top_k: int

    @classmethod
    def from_arguments(cls, arguments):
        try:
            args = SearchTextInput.model_validate(arguments)
        except ValidationError:
            return None
        # 统一全角/半角及大小写，再把标点和连续空白转换为词元间的单个空格。
        query = unicodedata.normalize("NFKC", args.query).lower()
        query = " ".join(re.findall(r"\w+", query))
        # 范围去重并排序，使论文列表顺序不影响比较；None（全库）与空列表分开。
        scope = None if args.paper_ids is None else tuple(sorted(set(args.paper_ids)))
        return cls(query, frozenset(query.split()), scope, args.top_k)

    def duplicates(self, previous):
        # 修改 K 或论文范围可能是在请求不同证据，不能只因查询相似就拦截。
        if self.paper_ids != previous.paper_ids or self.top_k != previous.top_k:
            return False
        if self.query == previous.query:
            return True
        # 数字目标、否定或比较词变化可能改变信息需求，保守放行这类查询。
        # ^ 是集合对称差：只留下两条查询之间新增或删去的词元。
        changed = self.tokens ^ previous.tokens
        if changed & QUERY_POLARITY_TOKENS or any(
            any(c.isdigit() for c in t) for t in changed
        ):
            return False
        # Jaccard = 共同词元数 / 全部不同词元数；空集合不参与除法。
        union = self.tokens | previous.tokens
        return bool(union) and (
            len(self.tokens & previous.tokens) / len(union) >= SEARCH_JACCARD_THRESHOLD
        )


def redundant_observation(name, previous_action, evidence_ids):
    """返回未执行的原因和可用证据，让模型自行结束或改换调查动作。"""
    return ToolObservation(
        status="error",
        tool_name=name,
        summary="A substantially similar search was already performed; requested search was not executed.",
        payload={
            "previous_action": previous_action,
            "existing_evidence_ids": sorted(evidence_ids),
            "instruction": "Determine whether existing evidence is sufficient. If not, identify a materially different information need, scope, or evidence source.",
            "failure": {
                "phase": "controller",
                "issue_code": "redundant_action",
                "retryable": False,
                "executed": False,
                "message": "Redundant search was not executed; no tool budget was consumed.",
            },
        },
        issue=ObservationIssue(code="redundant_action", retryable=False),
    )


def recovery_feedback(observation, valid_paper_ids):
    """在原观察结果上补充恢复建议，不改写调用参数，也不自动重试。"""
    if observation.status == "success":
        return observation
    code = observation.issue.code
    payload = dict(observation.payload)
    if code in {"unknown_paper", "invalid_arguments"}:
        payload.update(
            valid_paper_ids=valid_paper_ids,
            instruction="Correct the arguments before retrying. Choose valid paper IDs yourself; the controller has not changed your arguments.",
        )
    elif code == "empty_result":
        payload["instruction"] = (
            "The tool completed successfully but returned no evidence. Reformulate the query, change evidence source, or finish with insufficient evidence."
        )
    elif code == "dependency_failure":
        payload["instruction"] = (
            "The dependency may recover. Decide whether to retry, change strategy, or finish with explicit limitations; no automatic retry occurred."
        )
    elif code == "internal_failure":
        payload["instruction"] = (
            "The tool failed internally. Change strategy or finish with explicit limitations; do not fabricate evidence."
        )
    # 对模型而言未知论文属于非法参数；同时保留原领域错误码便于排查。
    if code == "unknown_paper":
        payload["domain_issue_code"] = code
    return ToolObservation(
        status=observation.status,
        tool_name=observation.tool_name,
        summary=observation.summary,
        payload=payload,
        issue=ObservationIssue(
            code="invalid_arguments" if code == "unknown_paper" else code,
            retryable=observation.issue.retryable,
        ),
    )
