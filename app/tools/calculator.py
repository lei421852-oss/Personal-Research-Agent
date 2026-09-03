"""A calculator implemented with a strict arithmetic AST evaluator."""

import ast
import operator
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, StrictStr

from app.tools.base import Tool

Number = int | float


class CalculatorInput(BaseModel):
    """Validated input accepted by the calculator."""

    model_config = ConfigDict(extra="forbid")

    expression: StrictStr


class CalculatorTool(Tool[CalculatorInput, Number]):
    """Evaluate arithmetic without executing arbitrary Python code."""

    name = "calculator"
    description = "Evaluate an arithmetic expression using +, -, *, /, and parentheses."
    input_model = CalculatorInput

    _binary_operators: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }
    _unary_operators: dict[type[ast.unaryop], Callable[[Number], Number]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    def execute(self, arguments: CalculatorInput) -> Number:
        try:
            expression = ast.parse(arguments.expression, mode="eval")
        except (SyntaxError, ValueError) as exc:
            raise ValueError("Invalid arithmetic expression") from exc
        return self._evaluate(expression.body)

    def _evaluate(self, node: ast.AST) -> Number:
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ValueError("Only numeric literals are allowed")
            return node.value

        if isinstance(node, ast.BinOp) and type(node.op) in self._binary_operators:
            operation = self._binary_operators[type(node.op)]
            return operation(self._evaluate(node.left), self._evaluate(node.right))

        if isinstance(node, ast.UnaryOp) and type(node.op) in self._unary_operators:
            operation = self._unary_operators[type(node.op)]
            return operation(self._evaluate(node.operand))

        raise ValueError("Expression contains an unsupported operation")
