"""单次工具执行边界，不调用模型、不规划、不重试，也不承担 Agent 循环。"""

from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.agent.errors import ToolNotFoundError
from app.diagnostics import failure_details
from app.evidence.citations import CitationValidationError
from app.evidence.errors import EvidenceDependencyError, UnknownPaperError
from app.evidence.resolver import EvidenceResolutionError
from app.papers.evidence_access import PaperEvidenceUnavailable
from app.papers.expansion import QueryExpansionError
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
        failure_exc = None
        try:
            if not isinstance(name, str) or not name.strip():
                raise ToolNotFoundError("invalid")
            tool = self.registry.get(name)
            validated = tool.input_model.model_validate(arguments)
            phase = "execution"
            result = tool.execute(validated)
            if not isinstance(result, ToolObservation) or result.tool_name != name:
                raise TypeError("Tool does not implement the observation contract")
            # 创建校验后的脱敏副本，保留原语料数据。
            observation = ToolObservation.model_validate(
                public_value(result.model_dump(mode="json"))
            )
        except ToolNotFoundError as exc:
            failure_exc = exc
            observation = self._error(
                safe_name, "unknown_tool", "Tool is not registered."
            )
        except ValidationError as exc:
            failure_exc = exc
            # 参数校验失败归为 invalid_arguments；工具输出格式错误归为 internal_failure。
            code = "internal_failure" if phase == "execution" else "invalid_arguments"
            summary = (
                "Tool returned an invalid result."
                if code == "internal_failure"
                else "Arguments do not satisfy this tool's schema."
            )
            observation = self._error(safe_name, code, summary)
        except QueryExpansionError as exc:
            failure_exc = exc
            observation = self._error(
                safe_name,
                "invalid_expansion",
                "Expansion output violated the original-query, nonempty or duplicate constraints.",
            )
        except EvidenceResolutionError as exc:
            failure_exc = exc
            observation = self._error(
                safe_name,
                "invalid_evidence",
                "Evidence identity, modality or context scope is invalid.",
            )
        except CitationValidationError as exc:
            failure_exc = exc
            observation = self._error(
                safe_name,
                "citation_validation_failure",
                "Model citations are empty or outside the supplied evidence.",
            )
        except PaperEvidenceUnavailable as exc:
            failure_exc = exc
            observation = ToolObservation(
                status="empty",
                tool_name=safe_name,
                summary="Discovered paper has no prepared evidence; no PDF was parsed.",
                payload={
                    "paper_discovered": True,
                    "paper_evidence_available": False,
                    "availability_status": "unavailable",
                    "unavailable_paper_ids": [exc.paper_id],
                },
                issue=ObservationIssue(code="empty_result", retryable=False),
            )
        except UnknownPaperError as exc:
            failure_exc = exc
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
        ) as exc:
            failure_exc = exc
            observation = self._error(
                safe_name,
                "dependency_failure",
                "A tool dependency is temporarily unavailable.",
            )
        except Exception as exc:  # noqa: BLE001 - 公开边界不得暴露意外异常的内部信息
            failure_exc = exc
            observation = self._error(
                safe_name, "internal_failure", "Tool execution failed internally."
            )
        if observation.status != "success":
            failure = (
                failure_details(failure_exc, phase=phase, summary=observation.summary)
                if failure_exc is not None
                else {"phase": phase, "message": observation.summary}
            )
            failure["issue_code"] = observation.issue.code
            failure["retryable"] = observation.issue.retryable
            observation = ToolObservation.model_validate(
                observation.model_dump()
                | {"payload": observation.payload | {"failure": failure}}
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
                retrieved_paper_ids=[
                    p["paper_id"]
                    for p in observation.payload.get("papers", [])
                    if observation.status == "success"
                    and isinstance(p, dict)
                    and isinstance(p.get("paper_id"), str)
                    and p["paper_id"].strip()
                ],
                failure=observation.payload.get("failure"),
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
