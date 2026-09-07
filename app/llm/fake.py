"""Deterministic LLM client for unit tests and local architecture checks."""

from copy import deepcopy

from app.llm.client import LLMClient, Message, ToolSchema
from app.llm.models import LLMResponse


class FakeLLMClient(LLMClient):
    """Return a configured response while recording the request."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
            }
        )
        return self.response.model_copy(deep=True)
