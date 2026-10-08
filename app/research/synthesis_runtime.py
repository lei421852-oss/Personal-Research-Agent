"""程序组装权威事实和最小降级报告；一次研究只有一个综合任务。"""

import asyncio

from app.research.synthesis_models import (
    ReportSection,
    ResearchReport,
    SynthesisMetrics,
)
from app.research.synthesis_validator import SynthesisOutputValidator


def assemble_report(source, outcome):
    dimensions = {d.dimension_id: d for d in source.plan.dimensions}
    cells = {c.requirement_id: c for c in source.cells}
    if outcome.status == "completed":
        proposal = SynthesisOutputValidator().validate(outcome.proposal, source)
        sections = [
            ReportSection(
                dimension_id=s.dimension_id,
                title=f"{dimensions[s.dimension_id].name} · {s.title}",
                cells=[cells[c.requirement_id] for c in s.cells],
                comparisons=s.comparisons,
            )
            for s in proposal.sections
        ]
        title = proposal.title
    else:
        sections = [
            ReportSection(
                dimension_id=d.dimension_id,
                title=d.name,
                cells=[c for c in source.cells if c.dimension_id == d.dimension_id],
            )
            for d in source.plan.dimensions
        ]
        title = "Verified findings and evidence gaps"
    total, verified = len(source.cells), len(source.verified_claims)
    attempted = int(bool(verified))
    success = int(outcome.status == "completed")
    degraded = outcome.status == "generation_degraded"
    metrics = SynthesisMetrics(
        total_requirements=total,
        verified_requirements=verified,
        missing_requirements=total - verified,
        rejected_requirements=sum(
            m.status == "review_rejected" for m in source.missing_evidence
        ),
        resolved_requirements=source.resolved_requirements,
        reviewed_requirements=source.reviewed_requirements,
        citation_validation_failures=outcome.citation_validation_failures,
        synthesis_model_calls=outcome.model_calls,
        protocol_corrections=outcome.protocol_corrections,
        provider_retries=outcome.provider_retries,
        synthesis_latency_ms=outcome.latency_ms,
        resolution_rate=source.resolved_requirements / total,
        evidence_coverage=verified / total,
        review_pass_rate=verified / source.reviewed_requirements
        if source.reviewed_requirements
        else None,
        report_generation_attempts=attempted,
        report_generation_successes=success,
        report_generation_success_rate=success / attempted if attempted else None,
        report_status=outcome.status,
        degraded_generation=degraded,
    )
    limitations = [
        "Only reviewed claims and prepared evidence are factual report content.",
        "Structural citation validation does not prove semantic entailment or model-review correctness.",
        "Missing cells are not completed from paper profiles or model knowledge.",
    ]
    if degraded:
        limitations.append(
            "Model generation failed; this deterministic report adds no technical conclusions."
        )
    if outcome.status == "cancelled":
        limitations.append(
            "Synthesis was cancelled; this is a facts snapshot, not a completed generation."
        )
    return ResearchReport(
        title=title,
        question=source.plan.question,
        paper_ids=source.plan.paper_ids,
        sections=sections,
        verified_claims=source.verified_claims,
        missing_evidence=source.missing_evidence,
        summary=f"{verified} of {total} planned requirements have reviewed evidence; {total - verified} remain missing."
        if verified
        else "当前没有足够已审核证据；所有计划单元均保留真实缺失状态。",
        limitations=limitations,
        status=outcome.status,
        generation_degraded=degraded,
        issue=outcome.issue,
        metrics=metrics,
        trace=outcome.trace,
    )


class SynthesisRuntime:
    def __init__(self, input_builder, agent):
        self.input_builder, self.agent = input_builder, agent
        self.last_report = None
        self.last_input = None
        self._running = False

    async def run(self, plan, worker_results, review_results):
        if self._running:
            raise RuntimeError(
                "A synthesis runtime cannot run two research tasks concurrently"
            )
        self._running = True
        self.last_report = None
        self.last_input = None
        try:
            source = self.input_builder.build(plan, worker_results, review_results)
            self.last_input = source
            try:
                outcome = await self.agent.run(source)
            except asyncio.CancelledError:
                self.last_report = assemble_report(source, self.agent.last_outcome)
                raise
            self.last_report = assemble_report(source, outcome)
            return self.last_report
        finally:
            self._running = False

    async def drain(self):
        await self.agent.drain()
