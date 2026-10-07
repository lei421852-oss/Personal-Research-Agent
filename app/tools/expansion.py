"""Query Expansion 是生成工具，不执行 Paper Retrieval。"""

from app.evidence.models import NonEmpty
from app.tools.base import Tool
from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.paper import PaperToolInput


class ExpandSearchQueryInput(PaperToolInput):
    query: NonEmpty


class ExpandSearchQueryTool(Tool[ExpandSearchQueryInput, ToolObservation]):
    name = "expand_search_query"
    description = (
        "Generate at most two faithful alternative queries; does NOT search papers. "
        "Use the exact original research request as query. Usually unnecessary for named "
        "papers or highly specific known-item searches. For open set-valued discovery, "
        "use only if the original search leaves meaningful perspectives uncovered. "
        "One expansion attempt per request. Keep the original query and execute approved "
        "alternatives separately with search_papers. Do not invent paper or method names."
    )
    input_model = ExpandSearchQueryInput

    def __init__(self, expander):
        self.expander = expander

    def execute(self, arguments):
        output = self.expander.expand(arguments.query)
        return ToolObservation(
            status="success" if output.expanded_queries else "empty",
            tool_name=self.name,
            summary="Generated retrieval queries only; no index or embedding was accessed.",
            payload=output.model_dump(),
            issue=None
            if output.expanded_queries
            else ObservationIssue(code="empty_result", retryable=False),
        )
