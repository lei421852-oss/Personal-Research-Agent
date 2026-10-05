import pytest
from pydantic import ValidationError

from app.tools.observations import ObservationIssue, ToolObservation


def test_success_empty_and_error_are_distinct():
    success = ToolObservation(
        status="success",
        tool_name="search_text",
        summary="Found",
        payload={"evidence": [{"evidence_id": "ev1"}]},
    )
    empty = ToolObservation(
        status="empty",
        tool_name="search_text",
        summary="No results",
        payload={},
        issue=ObservationIssue(code="empty_result", retryable=False),
    )
    failure = ToolObservation(
        status="error",
        tool_name="search_text",
        summary="Unavailable",
        payload={},
        issue=ObservationIssue(code="dependency_failure", retryable=True),
    )
    assert success.result_count == 1
    assert empty.result_count == failure.result_count == 0
    assert len({x.status for x in [success, empty, failure]}) == 3


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        ("empty", "dependency_failure", True),
        ("error", "empty_result", False),
        ("error", "invalid_arguments", True),
        ("error", "dependency_failure", False),
    ],
)
def test_invalid_status_issue_pairs_rejected(status, code, retryable):
    with pytest.raises(ValidationError):
        ToolObservation(
            status=status,
            tool_name="search_text",
            summary="x",
            payload={},
            issue=ObservationIssue(code=code, retryable=retryable),
        )


def test_success_without_evidence_rejected_for_paper_tools():
    with pytest.raises(ValidationError):
        ToolObservation(
            status="success",
            tool_name="search_text",
            summary="x",
            payload={"evidence": []},
        )
