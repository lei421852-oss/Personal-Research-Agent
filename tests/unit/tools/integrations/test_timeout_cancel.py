"""真实本地阻塞进程的超时、取消与清理；不使用远程请求。"""

import json
import sys
import threading
import time

import pytest

from app.tools.executor import ToolExecutor
from app.tools.integrations.adapter import LocalStdioSource
from app.tools.integrations.error_mapper import ExternalToolError
from app.tools.integrations.models import ExternalServiceConfig
from app.tools.integrations.transport import StdioToolClient
from app.tools.registry import ToolRegistry
from tests.unit.tools.integrations.conftest import MOCK


def wait_for_call(log):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if log.exists() and any(
            json.loads(x)["operation"] == "call_tool"
            for x in log.read_text().splitlines()
        ):
            return
        time.sleep(0.005)
    raise AssertionError("Local mock call did not start")


@pytest.mark.parametrize("mode", ["sleep", "no_exit"])
def test_timeout_is_bounded_and_not_empty(integration, mode):
    session, registry, _, _ = integration(mode, timeout_seconds=0.4)
    started = time.monotonic()
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert result.status == "error"
    assert result.payload["external_error_code"] == "timeout"
    assert time.monotonic() - started < 1.2
    assert result.payload["execution"]["attempts"] == 1
    assert result.payload["execution"]["automatic_retries"] == 0
    assert result.payload["execution"]["cleanup"]["reaped"] is True
    assert not session.client.active_pids


def test_cancelled_call_stops_and_reaps_child(integration):
    session, registry, log, _ = integration("sleep")
    session.cancel_event = threading.Event()
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
        )
    )
    thread.start()
    try:
        wait_for_call(log)
        session.cancel_event.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        result = results[0]
        assert result.payload["external_error_code"] == "cancelled"
        assert result.issue.retryable is False
        assert result.payload["execution"]["cleanup"]["reaped"]
    finally:
        session.cancel_event.set()
        thread.join(timeout=1)


def test_close_during_active_call_is_managed_and_idempotent(integration):
    session, registry, log, _ = integration("sleep")
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
        )
    )
    thread.start()
    try:
        wait_for_call(log)
        session.close()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert results[0].payload["external_error_code"] == "closed"
        assert not session.client.active_pids
        session.close()
    finally:
        session.close()
        thread.join(timeout=1)


def test_cancellation_before_execution_does_not_start_process(integration):
    session, registry, log, _ = integration()
    session.cancel_event = threading.Event()
    session.cancel_event.set()
    result = ToolExecutor(registry).execute("external__fixture__echo", {"query": "x"})
    assert result.payload["external_error_code"] == "cancelled"
    assert result.payload["execution"]["attempts"] == 0
    assert len(log.read_text().splitlines()) == 1


def test_lock_wait_is_included_in_same_deadline(integration):
    session, registry, log, _ = integration(timeout_seconds=0.2)
    session.client._call_lock.acquire()
    try:
        started = time.monotonic()
        result = ToolExecutor(registry).execute(
            "external__fixture__echo", {"query": "x"}
        )
        assert result.payload["external_error_code"] == "timeout"
        assert result.payload["execution"]["attempts"] == 0
        assert time.monotonic() - started < 0.8
        assert len(log.read_text().splitlines()) == 1
    finally:
        session.client._call_lock.release()


def test_discovery_timeout_still_cleans_process(integration):
    session, registry, _, _ = integration(
        "discovery_sleep", register=False, timeout_seconds=0.3
    )
    with pytest.raises(ExternalToolError, match="timeout"):
        session.register(registry)
    assert not registry.schemas() and not session.client.active_pids
    assert session.client.records[-1]["cleanup"]["reaped"]


def test_input_pipe_not_read_cannot_block_parent():
    config = ExternalServiceConfig(
        provider="fixture", enabled=True, timeout_seconds=0.3, max_input_bytes=1048576
    )
    client = StdioToolClient((sys.executable, str(MOCK), "no_input_read"), config)
    started = time.monotonic()
    try:
        with pytest.raises(ExternalToolError, match="timeout") as error:
            client.request("call_tool", name="echo", arguments={"query": "x" * 200000})
        assert error.value.execution["cleanup"]["reaped"]
        assert time.monotonic() - started < 1.0
    finally:
        client.close()


def test_cleanup_failure_is_fail_closed_and_prevents_second_process(
    tmp_path, monkeypatch
):
    config = ExternalServiceConfig(
        provider="fixture", enabled=True, allowed_tools=("echo",)
    )
    source = LocalStdioSource(
        config, (sys.executable, str(MOCK), "normal", str(tmp_path / "calls"))
    )
    session = source.open()
    registry = ToolRegistry()
    session.register(registry)
    original = session.client._cleanup_process

    def cleaned_but_unconfirmed(process, deadline):
        assert original(process, deadline)
        return False  # 确定性模拟“无法确认”，实际测试子进程仍已回收。

    monkeypatch.setattr(session.client, "_cleanup_process", cleaned_but_unconfirmed)
    executor = ToolExecutor(registry)
    first = executor.execute("external__fixture__echo", {"query": "x"})
    second = executor.execute("external__fixture__echo", {"query": "x"})
    assert first.payload["external_error_code"] == "cleanup_failed"
    assert second.payload["external_error_code"] == "cleanup_failed"
    assert second.payload["execution"]["attempts"] == 0
    assert sum(x["attempts"] for x in session.client.records) == 2  # 发现+首次调用。
    with pytest.raises(ExternalToolError, match="cleanup_failed"):
        session.close()
