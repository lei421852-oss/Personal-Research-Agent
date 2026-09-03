from typing import ClassVar

import pytest
from pydantic import BaseModel

from app.agent.errors import (
    InvalidToolArgumentsError,
    ToolExecutionError,
    ToolNotFoundError,
)
from app.tools.base import Tool
from app.tools.calculator import CalculatorTool
from app.tools.registry import ToolRegistry


class FailingInput(BaseModel):
    value: int


class FailingTool(Tool[FailingInput, None]):
    name: ClassVar[str] = "failing"
    description: ClassVar[str] = "Always fails for testing."
    input_model: ClassVar[type[BaseModel]] = FailingInput

    def execute(self, arguments: FailingInput) -> None:
        raise RuntimeError(f"failure for {arguments.value}")


def test_register_and_get_tool() -> None:
    registry = ToolRegistry()
    calculator = CalculatorTool()

    registry.register(calculator)

    assert registry.get("calculator") is calculator


def test_schemas_returns_tool_metadata_and_input_schema() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    schemas = registry.schemas()

    assert schemas[0]["name"] == "calculator"
    assert schemas[0]["description"]
    assert schemas[0]["input_schema"]["required"] == ["expression"]
    assert schemas[0]["input_schema"]["properties"]["expression"]["type"] == "string"


def test_get_unknown_tool_raises_domain_error() -> None:
    with pytest.raises(ToolNotFoundError, match="missing"):
        ToolRegistry().get("missing")


@pytest.mark.parametrize("arguments", [{}, {"expression": 123}])
def test_execute_rejects_invalid_arguments(arguments: object) -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    with pytest.raises(InvalidToolArgumentsError) as error:
        registry.execute("calculator", arguments)

    assert error.value.tool_name == "calculator"
    assert error.value.errors


def test_execute_normalizes_tool_failure() -> None:
    registry = ToolRegistry()
    registry.register(FailingTool())

    with pytest.raises(ToolExecutionError, match="failure for 7") as error:
        registry.execute("failing", {"value": 7})

    assert isinstance(error.value.__cause__, RuntimeError)
