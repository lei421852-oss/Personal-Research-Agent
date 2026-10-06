"""Deterministic action comparisons and actionable feedback, never auto-routing."""

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
        query = unicodedata.normalize("NFKC", args.query).lower()
        query = " ".join(re.findall(r"\w+", query))
        scope = None if args.paper_ids is None else tuple(sorted(set(args.paper_ids)))
        return cls(query, frozenset(query.split()), scope, args.top_k)

    def duplicates(self, previous):
        # Increasing K or changing scope is a materially different evidence request.
        if self.paper_ids != previous.paper_ids or self.top_k != previous.top_k:
            return False
        if self.query == previous.query:
            return True
        # Numeric targets and negation/comparison changes can alter the information need.
        changed = self.tokens ^ previous.tokens
        if changed & QUERY_POLARITY_TOKENS or any(
            any(c.isdigit() for c in t) for t in changed
        ):
            return False
        union = self.tokens | previous.tokens
        return bool(union) and (
            len(self.tokens & previous.tokens) / len(union) >= SEARCH_JACCARD_THRESHOLD
        )


def redundant_observation(name, previous_action, evidence_ids):
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
    # Unknown paper is invalid input at the policy boundary; retain domain diagnosis.
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
