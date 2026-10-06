"""Derived public decision context; no duplicated state or semantic decisions."""

from app.tools.redaction import public_value

EVIDENCE_SUMMARY_CHARS = 400


def build_decision_context(state, resolver):
    evidence = []
    for identity in sorted(state.observed_evidence_ids):
        item = resolver.resolve(identity)
        evidence.append(
            {
                "evidence_id": identity,
                "paper_id": item.paper_id,
                "page": item.page,
                "summary": item.text[:EVIDENCE_SUMMARY_CHARS],
            }
        )
    return public_value(
        {
            "question": state.question,
            "observed_evidence": evidence,
            "previous_actions": state.model_actions,
            "previous_failures": [
                o.model_dump(mode="json")
                for o in state.observations
                if o.status != "success"
            ],
            "budget_used": state.tool_calls_used,
            "budget_remaining": state.max_tool_calls - state.tool_calls_used,
            "decision_steps_used": state.model_calls_used,
            "decision_steps_remaining": max(
                0, state.max_tool_calls + 1 - state.model_calls_used
            ),
        }
    )
