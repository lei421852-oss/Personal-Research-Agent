"""通过既有Registry/Executor验证真实本地stdio往返，非远程验收。"""

import json

import pytest

from app.tools.executor import ToolExecutor
from app.tools.integrations.error_mapper import ExternalToolError
from app.tools.integrations.models import ExternalServiceConfig


def calls(log):
    return (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )


def test_discovers_allowed_tools_and_result_enters_existing_trace(integration):
    session, registry, log, _ = integration()
    assert {x["name"] for x in registry.schemas()} == {
        "external__fixture__echo",
        "external__fixture__count",
    }
    assert "UNTRUSTED_DESCRIPTION" not in json.dumps(registry.schemas())
    executor = ToolExecutor(registry)
    observation = executor.execute("external__fixture__echo", {"query": "fixture"})
    assert observation.status == "success"
    assert observation.payload["external_result"] == {"query": "fixture"}
    assert observation.payload["trust"] == "untrusted_external_data"
    assert observation.payload["evidence_verified"] is False
    assert observation.result_count == 0
    assert executor.traces[0].tool_name == observation.tool_name
    assert executor.traces[0].latency_ms > 0
    assert observation.payload["execution"]["attempts"] == 1
    assert all(x["cleanup"]["reaped"] for x in session.client.records)
    assert len(calls(log)) == 2


@pytest.mark.parametrize(
    "arguments", [{}, {"query": 1}, {"query": "valid", "extra": 1}]
)
def test_invalid_arguments_never_reach_service(integration, arguments):
    _, registry, log, _ = integration()
    result = ToolExecutor(registry).execute("external__fixture__echo", arguments)
    assert result.status == "error" and result.issue.code == "invalid_arguments"
    assert calls(log) == [{"operation": "list_tools", "name": None}]


def test_disabled_source_does_not_start_process(integration):
    session, registry, log, _ = integration(enabled=False)
    assert registry.schemas() == [] and not log.exists()
    assert list(session.client.records) == []


def test_unauthorized_name_never_reaches_service(integration):
    _, registry, log, _ = integration()
    result = ToolExecutor(registry).execute(
        "external__fixture__forbidden", {"query": "x"}
    )
    assert result.issue.code == "unknown_tool"
    assert len(calls(log)) == 1


def test_side_effecting_definition_cannot_grant_own_permission(integration):
    session, registry, log, _ = integration("side_effecting", register=False)
    with pytest.raises(ExternalToolError, match="permission_denied"):
        session.register(registry)
    assert registry.schemas() == []
    assert calls(log) == [{"operation": "list_tools", "name": None}]


def test_duplicate_names_refuse_batch_without_overwriting(integration):
    session, registry, log, _ = integration(register=False)
    tools = session.discover()
    registry.register(tools[0])
    original = registry.get(tools[0].name)
    with pytest.raises(ExternalToolError, match="name_conflict"):
        session.register(registry)
    assert registry.get(tools[0].name) is original
    assert len(registry.schemas()) == 1
    assert all(x["operation"] == "list_tools" for x in calls(log))


@pytest.mark.parametrize(
    "mode, code",
    [
        ("unsupported_schema", "unsupported_schema"),
        ("duplicate_definitions", "invalid_output"),
    ],
)
def test_invalid_discovery_leaves_no_partial_registration(integration, mode, code):
    session, registry, _, _ = integration(mode, register=False)
    with pytest.raises(ExternalToolError, match=code):
        session.register(registry)
    assert registry.schemas() == []


@pytest.mark.parametrize(
    "mode,status,issue,code",
    [
        ("empty", "empty", "empty_result", None),
        ("unavailable", "error", "dependency_failure", "unavailable"),
        ("unknown_tool", "error", "unknown_tool", "unknown_tool"),
        ("invalid_arguments", "error", "invalid_arguments", "invalid_arguments"),
        ("permission_denied", "error", "invalid_arguments", "permission_denied"),
        ("invalid_json", "error", "internal_failure", "invalid_output"),
        ("duplicate_json", "error", "internal_failure", "invalid_output"),
        ("invalid_status", "error", "internal_failure", "invalid_output"),
        ("fake_empty", "error", "internal_failure", "invalid_output"),
        ("wrong_id", "error", "internal_failure", "invalid_output"),
        ("deep_output", "error", "internal_failure", "invalid_output"),
        ("exit_failure", "error", "dependency_failure", "unavailable"),
    ],
)
def test_empty_and_typed_failures_are_not_interchanged(
    integration, mode, status, issue, code
):
    session, registry, log, _ = integration(mode)
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert (result.status, result.issue.code) == (status, issue)
    assert result.payload.get("external_error_code") == code
    assert sum(x["operation"] == "call_tool" for x in calls(log)) == 1
    assert not session.client.active_pids


def test_secret_output_and_error_are_redacted(integration):
    _, registry, _, _ = integration("secret")
    executor = ToolExecutor(registry)
    result = executor.execute("external__fixture__echo", {"query": "x"})
    serialized = result.model_dump_json() + str(executor.traces)
    assert "sk-fixture" not in serialized and "/home/fixture" not in serialized
    assert "should-not-leak" not in serialized


def test_parent_credentials_are_not_inherited(integration, monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "local-test-only-never-a-real-password")
    monkeypatch.setenv("LLM_API_KEY", "local-test-only-never-a-real-key")
    _, registry, _, _ = integration("credentials")
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert result.payload["external_result"]["unexpected_environment_count"] == 0


def test_external_text_cannot_create_root_evidence_or_permissions(integration):
    _, registry, _, _ = integration("instructions")
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert result.status == "success" and result.result_count == 0
    assert "evidence" not in result.payload and "papers" not in result.payload
    assert "permissions" not in result.payload


def test_configuration_defaults_off_and_never_repeats_secret_values():
    assert (
        ExternalServiceConfig.from_environment({}, provider="fixture").enabled is False
    )
    with pytest.raises(
        ValueError, match="invalid_external_tool_configuration"
    ) as error:
        ExternalServiceConfig.from_environment(
            {"AGENT_EXTERNAL_TOOLS_ENABLED": "password=private"}, provider="fixture"
        )
    assert "private" not in str(error.value)


def test_published_schema_preserves_optional_semantics_without_default_null(
    integration,
):
    from app.tools.integrations.adapter import ExternalTool
    from app.tools.integrations.models import ExternalToolDefinition

    session, _, _, _ = integration(register=False)
    definition = ExternalToolDefinition(
        name="echo",
        description="untrusted",
        permission="read_only",
        input_schema={
            "type": "object",
            "properties": {
                "optional": {"type": "integer", "description": "untrusted annotation"}
            },
            "additionalProperties": False,
        },
    )
    tool = ExternalTool(session, definition)
    assert tool.schema()["input_schema"] == {
        "type": "object",
        "properties": {"optional": {"type": "integer"}},
        "additionalProperties": False,
    }
    assert "untrusted annotation" not in json.dumps(tool.schema())
    assert tool.input_model.model_validate({}).model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize("cause", ["timeout", "cancelled"])
def test_schema_mapping_keeps_deadline_and_cancellation_boundary(
    integration, monkeypatch, cause
):
    import threading
    import time

    from app.tools.integrations import adapter

    session, registry, _, _ = integration(register=False, timeout_seconds=0.4)
    session.cancel_event = threading.Event()
    clock = [time.monotonic()]
    original = adapter.compile_input_schema
    monkeypatch.setattr(adapter, "monotonic", lambda: clock[0])

    def compile_schema(schema):
        model = original(schema)
        if cause == "timeout":
            clock[0] += 1
        else:
            session.cancel_event.set()
        return model

    monkeypatch.setattr(adapter, "compile_input_schema", compile_schema)
    with pytest.raises(ExternalToolError, match=cause):
        session.register(registry)
    assert registry.schemas() == []
    assert not session.client.active_pids
