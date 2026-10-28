"""输入输出有界、拒绝畸形协议，并确认错误不会回显秘密。"""

import json

from app.tools.executor import ToolExecutor


def test_oversize_stdout_is_stopped_at_limit_plus_one(integration):
    session, registry, _, _ = integration("oversize", max_output_bytes=4096)
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert result.payload["external_error_code"] == "output_limit"
    assert result.payload["execution"]["output_bytes"] <= 4097
    assert result.payload["execution"]["cleanup"]["reaped"]
    assert not session.client.active_pids


def test_oversize_input_is_rejected_before_starting_call(integration):
    _, registry, log, _ = integration(max_input_bytes=128)
    result = ToolExecutor(registry).execute(
        "external__fixture__echo", {"query": "x" * 100}
    )
    assert result.payload["external_error_code"] == "input_limit"
    assert result.payload["execution"]["attempts"] == 0
    assert len(log.read_text().splitlines()) == 1


def test_unbounded_stderr_does_not_create_pipe_deadlock(integration):
    session, registry, _, _ = integration("noisy_stderr")
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert result.status == "success"
    assert session.client.records[-1]["cleanup"]["reaped"]


def test_server_exit_does_not_leak_stderr_or_command(integration):
    _, registry, _, _ = integration("exit_failure")
    executor = ToolExecutor(registry)
    result = executor.execute("external__fixture__echo", {"query": "x"})
    serialized = result.model_dump_json() + json.dumps(
        [t.model_dump(mode="json") for t in executor.traces]
    )
    assert result.status == "error"
    assert "should-not-leak" not in serialized
    assert "mock_service.py" not in serialized
    assert "Traceback" not in serialized
