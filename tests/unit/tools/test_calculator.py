import pytest

from app.agent.errors import ToolExecutionError
from app.tools.calculator import CalculatorTool
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    tool_registry = ToolRegistry()
    tool_registry.register(CalculatorTool())
    return tool_registry


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(10 - 4) / 2", 3.0),
        ("-3 + +5", 2),
        ("2.5 * 4", 10.0),
    ],
)
def test_calculator_executes_supported_arithmetic(
    registry: ToolRegistry, expression: str, expected: float
) -> None:
    assert registry.execute("calculator", {"expression": expression}) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "2 ** 8",
        "7 // 2",
        "abs(-1)",
        "__import__('os').system('echo unsafe')",
        "True + 1",
        "1 +",
    ],
)
def test_calculator_rejects_illegal_expressions(
    registry: ToolRegistry, expression: str
) -> None:
    with pytest.raises(ToolExecutionError):
        registry.execute("calculator", {"expression": expression})


def test_calculator_wraps_division_by_zero(registry: ToolRegistry) -> None:
    with pytest.raises(ToolExecutionError, match="division by zero"):
        registry.execute("calculator", {"expression": "1 / 0"})
