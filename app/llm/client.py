"""Abstract interface implemented by model-provider adapters."""

from abc import ABC, abstractmethod
from typing import Any, TypeAlias

from app.llm.models import LLMResponse

Message: TypeAlias = dict[str, Any]
ToolSchema: TypeAlias = dict[str, Any]


class LLMClient(ABC):
    """Complete messages using the supplied tools and normalize the response."""

    @abstractmethod
    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """Return a provider-neutral response.

        Concrete adapters are responsible for translating both outbound schemas
        and the provider's raw response format at this boundary.
        """
