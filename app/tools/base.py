"""所有工具实现的共同抽象。"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

ToolInput = TypeVar("ToolInput", bound=BaseModel)
ToolResult = TypeVar("ToolResult")


class Tool(ABC, Generic[ToolInput, ToolResult]):
    """带名称、说明和输入校验的可执行能力。"""

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    def schema(self) -> dict[str, Any]:
        """返回工具名称、说明及输入的 JSON Schema。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }

    @abstractmethod
    def execute(self, arguments: ToolInput) -> ToolResult:
        """执行已经完成参数校验的工具能力。"""
