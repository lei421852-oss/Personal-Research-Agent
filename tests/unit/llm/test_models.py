import pytest
from pydantic import ValidationError

from app.llm.models import LLMResponse, ToolCall


def test_llm_response_defaults_to_no_tool_calls() -> None:
    response = LLMResponse(content="hello")

    assert response.content == "hello"
    assert response.tool_calls == []


def test_tool_call_requires_structured_arguments() -> None:
    with pytest.raises(ValidationError):
        ToolCall(id="call-1", name="calculator", arguments="not-an-object")
