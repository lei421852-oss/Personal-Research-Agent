"""Agent domain package."""

from app.agent.errors import (
    InvalidToolArgumentsError,
    ToolExecutionError,
    ToolNotFoundError,
)


# Lazy export prevents tools -> registry -> agent -> registry import cycles.
def __getattr__(name: str):
    if name == "SingleStepToolCaller":
        from app.agent.tool_calling import SingleStepToolCaller

        return SingleStepToolCaller
    raise AttributeError(name)


__all__ = [
    "InvalidToolArgumentsError",
    "SingleStepToolCaller",
    "ToolExecutionError",
    "ToolNotFoundError",
]
