"""工具注册与统一执行入口。"""

from typing import Any

from pydantic import ValidationError

from app.agent.errors import (
    InvalidToolArgumentsError,
    ToolExecutionError,
    ToolNotFoundError,
)
from app.tools.base import Tool


class ToolRegistry:
    """管理工具发现、输入校验和异常归一化。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any]) -> None:
        """注册工具；拒绝重复名称，避免调用目标不明确。"""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any]:
        """按名称查找工具；未注册时抛出领域异常。"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def schemas(self) -> list[dict[str, Any]]:
        """按注册顺序返回工具 Schema，保证顺序稳定。"""
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Any) -> Any:
        """校验参数后执行工具，并转换为统一异常。"""
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
