from typing import ClassVar

from pydantic import BaseModel

from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.observations import ToolObservation
from app.tools.registry import ToolRegistry


class Input(BaseModel):
    text: str


class ObservedTool(Tool[Input, ToolObservation]):
    name: ClassVar[str] = "observed_test"
    description: ClassVar[str] = "Test public execution boundary."
    input_model: ClassVar[type[BaseModel]] = Input

    def execute(self, arguments):
        self.original = ToolObservation(
            status="success",
            tool_name=self.name,
            summary="Result",
            payload={"evidence": [{"text": arguments.text}]},
        )
        return self.original


def test_success_payload_and_trace_are_redacted_without_changing_domain_result(
    monkeypatch,
):
    monkeypatch.setenv("VLM_API_KEY", "secret-without-prefix-987654")
    registry = ToolRegistry()
    tool = ObservedTool()
    registry.register(tool)
    executor = ToolExecutor(registry)
    private = "secret-without-prefix-987654 /home/lei/private/file"
    result = executor.execute(tool.name, {"text": private}, tool_call_id="public-call")
    public = result.model_dump_json() + executor.traces[0].model_dump_json()
    assert "secret-without-prefix" not in public and "/home/" not in public
    assert tool.original.payload["evidence"][0]["text"] == private


def test_output_validation_errors_are_internal_failures_not_invalid_arguments():
    class InvalidOutput(ObservedTool):
        def execute(self, arguments):
            return ToolObservation(
                status="success", tool_name="search_text", summary="bad", payload={}
            )

    registry = ToolRegistry()
    registry.register(InvalidOutput())
    executor = ToolExecutor(registry)
    result = executor.execute("observed_test", {"text": "valid input"})
    assert result.issue.code == "internal_failure" and not result.issue.retryable
    assert len(executor.traces) == 1
