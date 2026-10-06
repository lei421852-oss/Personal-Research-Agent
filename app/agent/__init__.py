"""Agent 领域代码包。"""

from app.agent.errors import (
    InvalidToolArgumentsError,
    ToolExecutionError,
    ToolNotFoundError,
)


# 延迟导出类，避免 tools → registry → agent → registry 的循环导入。
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
