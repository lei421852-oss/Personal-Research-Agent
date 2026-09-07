"""Minimal single-step orchestration from an LLM decision to a tool result."""

from typing import Any

from app.llm.client import LLMClient
from app.tools.registry import ToolRegistry


class SingleStepToolCaller:
    """Execute at most the first tool call from one LLM response.

    This deliberately does not send the tool result back to the LLM and does
    not loop. If the model requests multiple tools, only the first is executed.
    """

    def __init__(self, llm: LLMClient, registry: ToolRegistry) -> None:
        self._llm = llm
        self._registry = registry

    def run(self, user_message: str) -> Any:
        """Return direct model text or the first requested tool's result."""
        response = self._llm.complete(
            messages=[{"role": "user", "content": user_message}],
            tools=self._registry.schemas(),
        )

        if not response.tool_calls:
            return response.content

        tool_call = response.tool_calls[0]
        return self._registry.execute(tool_call.name, tool_call.arguments)
