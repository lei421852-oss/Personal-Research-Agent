"""结构与引用校验，不把身份真实性误称为自然语言蕴含证明。"""

from app.research.synthesis_models import SynthesisIssue, SynthesisProposal


class SynthesisOutputError(ValueError):
    def __init__(self, code, *, citation=False):
        self.issue = SynthesisIssue(
            code=code,
            message="Use exactly the planned cells and their verified claim/evidence references; preserve missing statuses.",
        )
        self.citation = citation
        super().__init__(code)


class SynthesisOutputValidator:
    def validate(self, proposal, source):
        try:
            proposal = SynthesisProposal.model_validate(
                proposal
                if isinstance(proposal, dict)
                else proposal.model_dump(mode="python", warnings=False)
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise SynthesisOutputError("invalid_structure") from exc
        dimensions = {d.dimension_id for d in source.plan.dimensions}
        if (
            len(proposal.sections) != len(dimensions)
            or {s.dimension_id for s in proposal.sections} != dimensions
        ):
            raise SynthesisOutputError("invalid_dimension")
        expected = {c.requirement_id: c for c in source.cells}
        claims = {c.claim_id: c for c in source.verified_claims}
        selected = []
        for section in proposal.sections:
            for cell in section.cells:
                original = expected.get(cell.requirement_id)
                if (
                    original is None
                    or (cell.paper_id, cell.dimension_id)
                    != (original.paper_id, original.dimension_id)
                    or cell.dimension_id != section.dimension_id
                ):
                    raise SynthesisOutputError("invalid_cell_identity")
                if cell.status != original.status:
                    raise SynthesisOutputError("invalid_cell_status")
                if cell.claim_id != original.claim_id:
                    raise SynthesisOutputError("invalid_claim_reference", citation=True)
                required = claims[cell.claim_id].evidence_ids if cell.claim_id else []
                if len(set(cell.evidence_ids)) != len(cell.evidence_ids) or set(
                    cell.evidence_ids
                ) != set(required):
                    raise SynthesisOutputError(
                        "invalid_evidence_reference", citation=True
                    )
                selected.append(cell.requirement_id)
            for comparison in section.comparisons:
                if len(set(comparison.paper_ids)) != len(
                    comparison.paper_ids
                ) or not set(comparison.paper_ids).issubset(source.plan.paper_ids):
                    raise SynthesisOutputError("invalid_comparison_scope")
                if len(set(comparison.claim_ids)) != len(comparison.claim_ids):
                    raise SynthesisOutputError(
                        "duplicate_comparison_claim", citation=True
                    )
                items = [claims.get(identity) for identity in comparison.claim_ids]
                if any(c is None for c in items):
                    raise SynthesisOutputError(
                        "invalid_comparison_claim", citation=True
                    )
                if any(c.dimension_id != section.dimension_id for c in items) or {
                    c.paper_id for c in items
                } != set(comparison.paper_ids):
                    raise SynthesisOutputError("incomplete_comparison_evidence")
                required = {e for c in items for e in c.evidence_ids}
                if (
                    len(set(comparison.evidence_ids)) != len(comparison.evidence_ids)
                    or set(comparison.evidence_ids) != required
                ):
                    raise SynthesisOutputError(
                        "invalid_comparison_evidence", citation=True
                    )
        if len(set(selected)) != len(selected) or set(selected) != set(expected):
            raise SynthesisOutputError("incomplete_report_matrix")
        return proposal
