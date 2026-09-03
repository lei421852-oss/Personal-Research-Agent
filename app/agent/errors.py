"""Domain errors raised by the tool infrastructure."""

from typing import Any


class ToolError(Exception):
    """Base class for errors exposed by the tool domain."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool has not been registered."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' is not registered")


class InvalidToolArgumentsError(ToolError):
    """Raised when arguments do not satisfy a tool's input model."""

    def __init__(self, tool_name: str, errors: list[dict[str, Any]]) -> None:
        self.tool_name = tool_name
        self.errors = errors
        super().__init__(f"Invalid arguments for tool '{tool_name}'")


class ToolExecutionError(ToolError):
    """Raised when a tool fails after its arguments have been validated."""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        self.message = message
        super().__init__(f"Tool '{tool_name}' execution failed: {message}")
