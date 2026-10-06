"""Chat Completions adapter for the existing provider-neutral LLMClient.

Only public tool actions and request timing are retained; never assistant reasoning.
"""

import json
from time import perf_counter
from typing import Any

from app.llm.client import LLMClient, Message, ToolSchema
from app.llm.models import LLMResponse, ToolCall
from app.tools.redaction import public_value


class OpenAICompatibleLLMClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 60,
        sdk_client: Any = None,
    ):
        if not api_key or not model.strip() or not base_url.strip() or timeout <= 0:
            raise ValueError(
                "Explicit API key, model, base URL and positive timeout are required"
            )
        if sdk_client is None:
            from openai import OpenAI

            sdk_client = OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
            )
        self.client, self.model = sdk_client, model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, messages: list[Message], tools: list[ToolSchema]
    ) -> LLMResponse:
        started = perf_counter()
        event = {"status": "error", "tool_calls": []}
        try:
            provider_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=provider_tools,
                tool_choice="required",
                parallel_tool_calls=False,
            )
            if not response.choices:
                raise ValueError("Provider returned no choices")
            choice = response.choices[0]
            if choice.finish_reason not in ("tool_calls", "stop"):
                raise ValueError("Provider returned an incomplete action")
            calls = []
            for call in choice.message.tool_calls or []:
                if call.type != "function":
                    raise ValueError("Unsupported provider action type")
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise TypeError("Tool arguments must be a JSON object")
                calls.append(
                    ToolCall(id=call.id, name=call.function.name, arguments=arguments)
                )
            # Discard content/reasoning fields rather than persisting raw SDK responses.
            normalized = LLMResponse(content=None, tool_calls=calls)
            event = {
                "status": "success",
                "tool_calls": public_value([c.model_dump(mode="json") for c in calls]),
                "finish_reason": choice.finish_reason,
            }
            return normalized
        finally:
            event["latency_ms"] = (perf_counter() - started) * 1000
            self.calls.append(event)

    def close(self):
        self.client.close()
