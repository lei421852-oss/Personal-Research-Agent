"""将主Agent终止动作和真实来源映射为答案优先DTO，不宣称语义已审核。"""

from copy import deepcopy


def assemble_answer(result, proposal, context, resolver, budget):
    reports = [context.reports[k] for k in context.delivered_reports]
    latest = reports[-1] if reports else None
    detailed = deepcopy(latest["report"]) if latest else None
    kind = proposal.answer_kind if proposal else "insufficient"
    findings, sources = [], []
    claims = {
        c["claim_id"]: c
        for entry in reports
        for c in entry["report"].get("verified_claims", [])
    }
    if proposal:
        for identity in proposal.claim_ids:
            claim = claims[identity]
            findings.append(
                {
                    "text": claim["text"],
                    "claim_id": identity,
                    "paper_id": claim["paper_id"],
                    "evidence_ids": claim["evidence_ids"],
                    "review_status": "passed",
                }
            )
        if kind == "evidence":
            for identity in proposal.evidence_ids:
                evidence = resolver.resolve(identity)
                findings.append(
                    {
                        "text": evidence.text,
                        "paper_id": evidence.paper_id,
                        "evidence_ids": [identity],
                        "review_status": "not_reviewed",
                        "evidence": [
                            {
                                "evidence_id": identity,
                                "paper_id": evidence.paper_id,
                                "page": evidence.page,
                                "text": evidence.text,
                                "figure_id": getattr(evidence, "figure_id", None),
                                "caption": getattr(evidence, "caption", None),
                            }
                        ],
                    }
                )
        if kind == "metadata":
            candidates = {p["paper_id"]: p for p in context.candidates()}
            sources = [
                dict(candidates[i], source_kind="paper_metadata")
                for i in proposal.paper_ids
            ]
    citations = [c.model_dump(mode="json") for c in result.citations] if result else []
    failures = [o for o in context.observations if o.status == "error"]
    status = "answered" if proposal and kind != "insufficient" else "insufficient"
    if result and result.status not in {"completed", "insufficient_evidence"}:
        status = "failed"
    if kind == "insufficient" and failures:
        status = "failed"
    if latest and (
        latest["research_status"] != "completed"
        or detailed.get("status") != "completed"
        or detailed.get("missing_requirements")
    ):
        status = (
            "partial"
            if citations
            else "failed"
            if latest["research_status"] != "completed"
            else "insufficient"
        )
    limitations = list(
        proposal.limitations if proposal else (result.limitations if result else [])
    )
    if detailed:
        limitations.extend(detailed.get("limitations", []))
        if detailed.get("missing_requirements"):
            limitations.append(
                "The detailed research retains missing requirements; this is not a complete research conclusion."
            )
        sources.append(
            {
                "source_kind": "research_report",
                "report_reference": next(
                    k for k, v in context.reports.items() if v is latest
                ),
                "sha256": latest["sha256"],
                "research_status": latest["research_status"],
                "answer_review": "not_independently_reviewed",
            }
        )
    mode = (
        "multi_agent_research"
        if context.research_calls
        else "tools"
        if context.observations
        else "direct"
    )
    base = (
        deepcopy(detailed)
        if detailed
        else {
            "title": "Answer to the user's question",
            "markdown": "",
            "paper_ids": [],
            "verified_claims": [],
            "missing_requirements": [],
            "comparisons": [],
            "diagnostics": {},
            "human_review": "not_performed",
        }
    )
    direct = (
        proposal.answer
        if proposal
        else "The agent could not produce a validated answer. Available records and errors remain visible."
    )
    base.update(
        status="failed"
        if status == "failed"
        else "insufficient_evidence"
        if status == "insufficient"
        else "completed",
        question=context.original_question,
        original_question=context.original_question,
        execution_question=context.execution_question,
        answer_status=status,
        direct_answer=direct,
        key_findings=findings,
        citations=citations,
        limitations=list(dict.fromkeys(limitations)),
        detailed_report=detailed,
        execution_mode=mode,
        source_references=sources,
        answer_review_status="not_independently_reviewed",
        evidence_level=kind,
        metrics=(detailed.get("metrics", {}) if detailed else {}) | budget.snapshot(),
        generation_degraded=bool(detailed and detailed.get("generation_degraded")),
    )
    return base


def failure_answer(context, resolver, budget, *, cancelled=False):
    # 已有权威部分报告继续可查，但不会把失败任务包装成完整回答。
    for reference in context.reports:
        context.delivered_reports.add(reference)
    output = assemble_answer(None, None, context, resolver, budget)
    output.update(
        answer_status="cancelled" if cancelled else "failed",
        status="cancelled" if cancelled else "failed",
        direct_answer="任务已取消；已取得的真实结果和证据缺口保留。"
        if cancelled
        else "执行未能完成；目前无法给出可靠的完整回答，已有记录仍可查看。",
    )
    return output


def historical_answer(report, original_question=None):
    """只把原历史的不足与事实显示在首屏，不新写跨论文比较结论。"""
    result = deepcopy(report)
    missing = report.get("missing_requirements", [])
    claims = report.get("verified_claims", [])
    result.update(
        answer_status="partial" if claims else "insufficient",
        direct_answer="当前历史记录不足以完整回答这项研究问题。下方保留已审核事实、原引用及未完成需求；不能据此补写完整比较结论。"
        if missing
        else "以下为历史报告已审核的回答要点；没有重新执行研究或新增事实。",
        key_findings=[
            {
                "text": c["text"],
                "claim_id": c["claim_id"],
                "paper_id": c["paper_id"],
                "evidence_ids": [e["evidence_id"] for e in c["evidence"]],
                "review_status": "passed",
            }
            for c in claims
        ],
        citations=[],
        detailed_report=deepcopy(report),
        execution_mode="historical_replay",
        answer_review_status="not_independently_reviewed",
        original_question=original_question or report.get("question", ""),
    )
    return result
