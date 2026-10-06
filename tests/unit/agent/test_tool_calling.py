import pytest

from app.agent.errors import InvalidToolArgumentsError, ToolNotFoundError
from app.agent.tool_calling import SingleStepToolCaller
from app.llm.fake import FakeLLMClient
from app.llm.models import LLMResponse, ToolCall
from app.tools.calculator import CalculatorTool
from app.tools.registry import ToolRegistry


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def test_valid_calculator_tool_call_executes_and_receives_registry_schemas() -> None:
    registry = make_registry()
    llm = FakeLLMClient(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="calculator",
                    arguments={"expression": "(2 + 3) * 4"},
                )
            ]
        )
    )
    caller = SingleStepToolCaller(llm, registry)

    result = caller.run("Calculate (2 + 3) * 4")

    assert result == 20
    assert llm.calls == [
        {
            "messages": [{"role": "user", "content": "Calculate (2 + 3) * 4"}],
            "tools": registry.schemas(),
        }
    ]


def test_unknown_tool_error_is_preserved() -> None:
    llm = FakeLLMClient(
        LLMResponse(
            tool_calls=[
                ToolCall(id="call-1", name="weather", arguments={"city": "Paris"})
            ]
        )
    )

    with pytest.raises(ToolNotFoundError, match="weather"):
        SingleStepToolCaller(llm, make_registry()).run("What is the weather?")


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"expression": 123},
        {"expression": "1 + 2", "unexpected": True},
    ],
)
def test_invalid_llm_arguments_are_validated_by_registry(
    arguments: dict[str, object],
) -> None:
    llm = FakeLLMClient(
        LLMResponse(
            tool_calls=[ToolCall(id="call-1", name="calculator", arguments=arguments)]
        )
    )

    with pytest.raises(InvalidToolArgumentsError):
        SingleStepToolCaller(llm, make_registry()).run("Calculate something")


def test_direct_text_response_is_returned_without_tool_execution() -> None:
    llm = FakeLLMClient(LLMResponse(content="No calculation is needed."))

    result = SingleStepToolCaller(llm, make_registry()).run("Say hello")

    assert result == "No calculation is needed."


def test_multiple_tool_calls_execute_only_the_first() -> None:
    llm = FakeLLMClient(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="calculator",
                    arguments={"expression": "1 + 1"},
                ),
                ToolCall(
                    id="call-2",
                    name="calculator",
                    arguments={"expression": "100 + 100"},
                ),
            ]
        )
    )

    result = SingleStepToolCaller(llm, make_registry()).run("Calculate twice")

    assert result == 2
