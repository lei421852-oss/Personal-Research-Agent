"""从一次模型决策执行到工具结果的最小单步编排。"""

from typing import Any

from app.llm.client import LLMClient
from app.tools.registry import ToolRegistry


class SingleStepToolCaller:
    """仅执行一次模型响应中的第一个工具调用。

    此历史单步实现不会把结果送回模型，也不会循环；多工具响应只执行第一个。
    当前有限步 Agent 的核心执行逻辑位于 BoundedToolAgent。
    """

    def __init__(self, llm: LLMClient, registry: ToolRegistry) -> None:
        self._llm = llm
        self._registry = registry

    def run(self, user_message: str) -> Any:
        """返回模型的直接文本，或其请求的第一个工具结果。"""
        response = self._llm.complete(
            messages=[{"role": "user", "content": user_message}],
            tools=self._registry.schemas(),
        )

        if not response.tool_calls:
            return response.content

        tool_call = response.tool_calls[0]
        return self._registry.execute(tool_call.name, tool_call.arguments)
