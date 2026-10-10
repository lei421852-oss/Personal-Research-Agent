"""长研究作为普通注册工具，由非冻结异步桥接回到既有研究链。"""

import hashlib
import json

from app.research.research_handoff import ResearchToolInput
from app.research.run_presenter import present_execution
from app.tools.base import Tool
from app.tools.observations import ObservationIssue, ToolObservation


class MultiAgentResearchTool(Tool):
    name = "run_multi_agent_research"
    description = (
        "Optionally investigate a specific research goal with the existing Planner, paper-local "
        "Workers, Reviewer, bounded Recovery and Synthesis. Use only when additional depth is "
        "needed; comparisons do not automatically require this tool. Select 1–3 lawful targets "
        "from observed retrieved candidates, explicitly named papers, or supplied scope. "
        "Unavailable full text stays missing. Return to the main agent after the report; "
        "this tool does not recursively call itself and may execute only once per run."
    )
    input_model = ResearchToolInput

    def __init__(self, context, budget, invoke, run_child, *, max_calls=1):
        self.context, self.budget = context, budget
        self.invoke, self.run_child, self.max_calls = invoke, run_child, max_calls

    def execute(self, arguments):
        try:
            handoff = self.context.handoff(arguments, self.budget)
        except ValueError:
            return self._error(
                "invalid_arguments", "研究交接来源或论文范围无效。", "invalid_handoff"
            )
        if self.context.research_calls >= self.max_calls:
            return self._error(
                "invalid_arguments",
                "本次运行的研究调用上限已用尽。",
                "research_call_limit",
            )
        # 至少为Planner及主Agent下一轮保留请求；不足时不启动子任务。
        if self.budget.maximum - self.budget.used < len(handoff.selected_paper_ids) + 3:
            return self._error(
                "invalid_arguments",
                "剩余模型预算不足，未启动研究。",
                "research_budget_unavailable",
            )
        self.context.research_calls += 1
        self.context.handoffs.append(handoff.model_dump(mode="json"))
        execution = self.invoke(lambda: self.run_child(handoff))
        original = present_execution(execution)
        # 公开报告的引用快照由程序投影为ID集合，不让主模型创建权威引用。
        for claim in original.get("verified_claims", []):
            claim["evidence_ids"] = [item["evidence_id"] for item in claim["evidence"]]
        report = execution.get("research", {}).get("outcome", {}).get("report")
        digest = hashlib.sha256(
            json.dumps(report or original, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        reference = "report_" + digest
        research_status = execution.get("research", {}).get(
            "status", "execution_failed"
        )
        self.context.reports[reference] = {
            "execution": execution,
            "report": original,
            "sha256": digest,
            "research_status": research_status,
        }
        claims = original.get("verified_claims", [])
        payload = {
            "research_status": research_status,
            "report_status": original["status"],
            "report_reference": reference,
            "source_report_sha256": digest,
            "verified_claim_references": claims,
            "missing_evidence": original.get("missing_requirements", []),
            "limitations": original.get("limitations", []),
            "execution_summary": original.get("metrics", {}),
            "budget_usage": self.budget.snapshot(),
            "handoff": handoff.model_dump(mode="json"),
        }
        if research_status != "completed":
            return ToolObservation(
                status="error",
                tool_name=self.name,
                summary="子研究失败；已审核的部分事实与真实缺口仍保留。",
                payload=payload,
                issue=ObservationIssue(code="internal_failure", retryable=False),
            )
        if not claims:
            return ToolObservation(
                status="empty",
                tool_name=self.name,
                summary="研究已结束，但没有足够已审核证据。",
                payload=payload,
                issue=ObservationIssue(code="empty_result", retryable=False),
            )
        payload["evidence"] = [e for claim in claims for e in claim["evidence"]]
        return ToolObservation(
            status="success",
            tool_name=self.name,
            summary="已返回权威研究结果；主Agent继续决定如何回答，缺失状态不能升级。",
            payload=payload,
        )

    def _error(self, code, summary, reason):
        return ToolObservation(
            status="error",
            tool_name=self.name,
            summary=summary,
            payload={"issue_code": reason, "budget_usage": self.budget.snapshot()},
            issue=ObservationIssue(code=code, retryable=False),
        )
