"""查询预算与来源协议的确定性校验，不用关键词判断是否应扩展。"""

from pydantic import ValidationError

from app.papers.expansion import (
    MAX_EXPANDED_QUERIES,
    MAX_EXPANSION_CALLS,
    normalize_query,
)
from app.tools.observations import ObservationIssue, ToolObservation


def expansion_calls_used(state):
    return sum(
        t.tool_name == "expand_search_query"
        and (not t.failure or t.failure.get("executed") is not False)
        for t in state.trace
    )


def approved_queries(state):
    return [
        q
        for o in state.observations
        if o.tool_name == "expand_search_query" and o.status == "success"
        for q in o.payload["expanded_queries"]
    ]


def reject(name, code, message):
    return ToolObservation(
        status="error",
        tool_name=name,
        summary=message,
        payload={
            "failure": {
                "phase": "controller",
                "issue_code": code,
                "retryable": False,
                "executed": False,
                "message": message,
            }
        },
        issue=ObservationIssue(code=code, retryable=False),
    )


def validate_query_action(state, call, registry):
    if call.name not in {"expand_search_query", "search_papers"}:
        return None
    if (
        call.name == "expand_search_query"
        and expansion_calls_used(state) >= MAX_EXPANSION_CALLS
    ):
        return reject(
            call.name,
            "expansion_budget_exceeded",
            "Expansion budget is exhausted; request was not executed. Use existing queries or finish.",
        )
    try:
        arguments = registry.get(call.name).input_model.model_validate(call.arguments)
    except ValidationError:
        return reject(
            call.name,
            "invalid_arguments",
            "Query tool arguments are invalid; request was not executed.",
        )
    if call.name == "expand_search_query":
        if arguments.query != state.question.strip():
            return reject(
                call.name,
                "invalid_arguments",
                "Expand the original research request exactly; do not substitute a narrower or answer-specific query.",
            )
        return None
    searches = [
        o
        for o in state.observations
        if o.tool_name == "search_papers" and o.status in {"success", "empty"}
    ]
    if not searches and arguments.query != state.question.strip():
        return reject(
            call.name,
            "invalid_arguments",
            "The original research query must be searched unchanged before alternatives.",
        )
    key = normalize_query(arguments.query)
    if any(normalize_query(o.payload["query"]) == key for o in searches):
        return reject(
            call.name,
            "redundant_action",
            "This paper query was already executed; duplicate request was not executed.",
        )
    allowed = [state.question.strip(), *approved_queries(state)[:MAX_EXPANDED_QUERIES]]
    if key not in {normalize_query(q) for q in allowed}:
        return reject(
            call.name,
            "invalid_arguments",
            "Search only the original query or a validated expanded query; generate alternatives through expand_search_query.",
        )
    return None
