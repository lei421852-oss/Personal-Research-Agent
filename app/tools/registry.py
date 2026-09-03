"""Registration and uniform execution entry point for tools."""

from typing import Any

from pydantic import ValidationError

from app.agent.errors import (
    InvalidToolArgumentsError,
    ToolExecutionError,
    ToolNotFoundError,
)
from app.tools.base import Tool


class ToolRegistry:
    """Owns tool discovery, input validation, and error normalization."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any]) -> None:
        """Register a tool, rejecting ambiguous duplicate names."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any]:
        """Find a tool by name or raise a domain error."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def schemas(self) -> list[dict[str, Any]]:
        """Return schemas in deterministic registration order."""
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Any) -> Any:
        """Validate arguments and execute a named tool."""
        tool = self.get(name)
        try:
            validated_arguments = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise InvalidToolArgumentsError(name, exc.errors()) from exc

        try:
            return tool.execute(validated_arguments)
        except ToolExecutionError:
            raise
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            raise ToolExecutionError(name, message) from exc
