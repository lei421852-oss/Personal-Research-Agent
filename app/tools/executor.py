"""One-call execution boundary. No LLM, planning, retries, or Agent Loop."""

from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.agent.errors import ToolNotFoundError
from app.evidence.errors import EvidenceDependencyError, UnknownPaperError
from app.retrieval.errors import RetrievalDependencyError
from app.tools.errors import DependencyFailure
from app.tools.observations import ObservationIssue, ToolExecutionTrace, ToolObservation
from app.tools.redaction import public_value, redact_text
from app.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._traces: list[ToolExecutionTrace] = []

    @property
    def traces(self) -> tuple[ToolExecutionTrace, ...]:
        return tuple(self._traces)

    def execute(
        self, name: str, arguments: Any, *, tool_call_id: str | None = None
    ) -> ToolObservation:
        started = perf_counter()
        safe_name = (
            redact_text(name)
            if isinstance(name, str) and name.strip()
            else "invalid_tool_name"
        )
        call_id = (
            redact_text(tool_call_id)
            if isinstance(tool_call_id, str) and tool_call_id.strip()
            else uuid4().hex
        )
        phase = "arguments"
        try:
            if not isinstance(name, str) or not name.strip():
                raise ToolNotFoundError("invalid")
            tool = self.registry.get(name)
            validated = tool.input_model.model_validate(arguments)
            phase = "execution"
            result = tool.execute(validated)
            if not isinstance(result, ToolObservation) or result.tool_name != name:
                raise TypeError("Tool does not implement the observation contract")
            # Construct a validated, redacted copy; original corpus stays untouched.
            observation = ToolObservation.model_validate(
                public_value(result.model_dump(mode="json"))
            )
        except ToolNotFoundError:
            observation = self._error(
                safe_name, "unknown_tool", "Tool is not registered."
            )
        except ValidationError:
            # Only argument validation is invalid_arguments; malformed tool output is internal_failure.
            code = "internal_failure" if phase == "execution" else "invalid_arguments"
            summary = (
                "Tool returned an invalid result."
                if code == "internal_failure"
                else "Arguments do not satisfy this tool's schema."
            )
            observation = self._error(safe_name, code, summary)
        except UnknownPaperError:
            observation = self._error(
                safe_name,
                "unknown_paper",
                "Requested paper is not in the prepared corpus.",
            )
        except (
            DependencyFailure,
            EvidenceDependencyError,
            RetrievalDependencyError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            observation = self._error(
                safe_name,
                "dependency_failure",
                "A tool dependency is temporarily unavailable.",
            )
        except Exception:  # noqa: BLE001 - public boundary must not expose unexpected failures
            observation = self._error(
                safe_name, "internal_failure", "Tool execution failed internally."
            )
        safe_arguments = public_value(arguments)
        if not isinstance(safe_arguments, dict):
            safe_arguments = {"invalid_input": safe_arguments}
        self._traces.append(
            ToolExecutionTrace(
                tool_call_id=call_id,
                tool_name=safe_name,
                arguments=safe_arguments,
                status=observation.status,
                latency_ms=(perf_counter() - started) * 1000,
                result_count=observation.result_count,
            )
        )
        return observation

    @staticmethod
    def _error(name, code, summary):
        return ToolObservation(
            status="error",
            tool_name=name,
            summary=summary,
            payload={},
            issue=ObservationIssue(code=code, retryable=code == "dependency_failure"),
        )
