"""Common abstraction implemented by every tool."""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

ToolInput = TypeVar("ToolInput", bound=BaseModel)
ToolResult = TypeVar("ToolResult")


class Tool(ABC, Generic[ToolInput, ToolResult]):
    """A named capability with documented, validated input and execution."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    def schema(self) -> dict[str, Any]:
        """Return the tool metadata and JSON Schema for its input."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }

    @abstractmethod
    def execute(self, arguments: ToolInput) -> ToolResult:
        """Execute the capability with already validated arguments."""
