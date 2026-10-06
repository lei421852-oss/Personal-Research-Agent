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


class ScriptedLLMClient(LLMClient):
    """Sequence of responses or context-aware callbacks, with complete request snapshots."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, *, messages, tools):
        self.calls.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        if not self.responses:
            raise RuntimeError("Scripted response sequence exhausted")
        step = self.responses.pop(0)
        response = step(messages, tools) if callable(step) else step
        return response.model_copy(deep=True)
