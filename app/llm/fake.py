"""用于单元测试和本地架构检查的确定性模型客户端。"""

from copy import deepcopy

from app.llm.client import LLMClient, Message, ToolSchema
from app.llm.models import LLMResponse


class FakeLLMClient(LLMClient):
    """返回预设响应，同时保存调用请求供测试核对。"""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
            }
        )
        return self.response.model_copy(deep=True)


class ScriptedLLMClient(LLMClient):
    """按顺序返回预设响应或调用上下文回调，并保存每次完整请求快照。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, *, messages, tools):
        self.calls.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        if not self.responses:
            raise RuntimeError("Scripted response sequence exhausted")
        step = self.responses.pop(0)
        response = step(messages, tools) if callable(step) else step
        return response.model_copy(deep=True)
