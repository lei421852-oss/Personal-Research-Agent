"""Two adapter-only checks; existing runtime tests remain unchanged."""

import json
from types import SimpleNamespace

import pytest

from app.llm.provider import OpenAICompatibleLLMClient


def sdk_response(arguments):
    call = SimpleNamespace(
        type="function",
        id="call-1",
        function=SimpleNamespace(name="finish", arguments=arguments),
    )
    choice = SimpleNamespace(
        finish_reason="tool_calls",
        message=SimpleNamespace(
            content="Do not retain hidden reasoning", tool_calls=[call]
        ),
    )
    return SimpleNamespace(choices=[choice])


def test_adapter_translates_tools_and_preserves_observation_feedback():
    class Completions:
        def create(self, **kwargs):
            self.request = kwargs
            return sdk_response(
                json.dumps(
                    {
                        "answer": "Insufficient",
                        "evidence_ids": [],
                        "limitations": ["No evidence"],
                    }
                )
            )

    endpoint = Completions()
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=endpoint))
    adapter = OpenAICompatibleLLMClient(
        api_key="fake",
        model="explicit-model",
        base_url="https://unused",
        sdk_client=sdk,
    )
    messages = [
        {"role": "user", "content": "Question"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "prev",
                    "type": "function",
                    "function": {"name": "search_text", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "prev", "content": '{"status":"empty"}'},
    ]
    schema = {
        "name": "finish",
        "description": "Finish",
        "input_schema": {"type": "object"},
    }
    result = adapter.complete(messages=messages, tools=[schema])
    assert endpoint.request["messages"] == messages
    assert endpoint.request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert endpoint.request["tool_choice"] == "required"
    assert endpoint.request["parallel_tool_calls"] is False
    assert result.tool_calls[0].arguments["evidence_ids"] == []
    assert result.content is None
    assert "hidden reasoning" not in json.dumps(adapter.calls)
    assert adapter.calls[0]["latency_ms"] >= 0


def test_adapter_rejects_invalid_json_without_storing_raw_content():
    sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: sdk_response("not-json")
            )
        )
    )
    adapter = OpenAICompatibleLLMClient(
        api_key="fake", model="explicit", base_url="https://unused", sdk_client=sdk
    )
    with pytest.raises(ValueError):
        adapter.complete(messages=[], tools=[])
    assert adapter.calls[0]["status"] == "error"
    assert "not-json" not in json.dumps(adapter.calls)
