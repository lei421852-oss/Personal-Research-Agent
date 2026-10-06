"""工具基础设施使用的领域异常。"""

from typing import Any


class ToolError(Exception):
    """工具领域对外异常的基类。"""


class ToolNotFoundError(ToolError):
    """请求的工具未注册时抛出。"""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' is not registered")


class InvalidToolArgumentsError(ToolError):
    """参数不满足工具输入模型时抛出。"""

    def __init__(self, tool_name: str, errors: list[dict[str, Any]]) -> None:
        self.tool_name = tool_name
        self.errors = errors
        super().__init__(f"Invalid arguments for tool '{tool_name}'")


class ToolExecutionError(ToolError):
    """参数已通过校验，但工具执行失败时抛出。"""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        self.message = message
        super().__init__(f"Tool '{tool_name}' execution failed: {message}")
