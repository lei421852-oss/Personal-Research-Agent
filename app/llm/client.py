"""由各模型提供方适配器实现的抽象接口。"""

from abc import ABC, abstractmethod
from typing import Any, TypeAlias

from app.llm.models import LLMResponse

Message: TypeAlias = dict[str, Any]
ToolSchema: TypeAlias = dict[str, Any]


class LLMClient(ABC):
    """用给定工具处理消息，并将模型响应转换为统一结构。"""

    @abstractmethod
    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """返回与提供方无关的响应。

        具体适配器负责在此边界转换工具 Schema 和模型提供方的原始响应格式。
        """
