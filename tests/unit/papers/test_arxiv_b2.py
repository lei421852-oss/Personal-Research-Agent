"""确定性预算、分类和进程监督边界；不连接外部服务。"""

import subprocess
import threading
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from io import BytesIO

import pytest

from app.papers.arxiv import (
    ArxivClient,
    ArxivFailure,
    HTTPResponse,
    retry_after_seconds,
    retry_eligible,
)
from app.papers.arxiv_transport import IsolatedArxivTransport
from app.tools.arxiv import SearchArxivTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from tests.unit.papers.test_arxiv import Clock, feed


class Sequence:
    def __init__(self, clock, responses, advance=0):
        self.clock, self.responses, self.advance = clock, list(responses), advance
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(timeout)
        self.clock.now += self.advance
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def test_retry_shares_original_deadline_and_is_one_tool_execution():
    clock = Clock()
    transport = Sequence(
        clock, [HTTPResponse(503, b"", "application/xml"), feed()], advance=1
    )
    client = ArxivClient(transport=transport, clock=clock, sleeper=clock.sleep)
    registry = ToolRegistry()
    registry.register(SearchArxivTool(client))
    executor = ToolExecutor(registry)
    observation = executor.execute("search_arxiv", {"query": "ti:fixture"})
    assert observation.status == "success" and len(executor.traces) == 1
    assert observation.payload["execution"]["attempts"] == 2
    assert transport.calls == [15, 11]
    assert observation.payload["execution"]["elapsed_ms"] == 5000


def test_retry_after_wait_does_not_refresh_timeout():
    clock = Clock()
    transport = Sequence(
        clock, [HTTPResponse(429, b"", "application/xml", "10"), feed()], advance=1
    )
    client = ArxivClient(transport=transport, clock=clock, sleeper=clock.sleep)
    result = client.search("ti:fixture")
    assert transport.calls == [15, 4] and clock.delays == [10]
    assert result.execution["attempts"] == 2


def test_retry_budget_insufficient_preserves_known_failure():
    clock = Clock()
    transport = Sequence(
        clock, [HTTPResponse(503, b"", "application/xml")], advance=0.5
    )
    client = ArxivClient(
        timeout_seconds=1, transport=transport, clock=clock, sleeper=clock.sleep
    )
    with pytest.raises(ArxivFailure) as exc:
        client.search("ti:fixture")
    assert exc.value.code == "upstream_http_503" and len(transport.calls) == 1
    assert exc.value.execution["stop_reason"] == "insufficient_retry_budget"


def test_deadline_after_parse_never_returns_late_success():
    clock = Clock()
    transport = Sequence(clock, [feed()], advance=2)
    client = ArxivClient(
        timeout_seconds=1, transport=transport, clock=clock, sleeper=clock.sleep
    )
    with pytest.raises(ArxivFailure) as exc:
        client.search("ti:fixture")
    assert exc.value.category == "timeout" and len(transport.calls) == 1


def test_precancelled_call_has_zero_attempts():
    clock = Clock()
    transport = Sequence(clock, [feed()])
    cancel = threading.Event()
    cancel.set()
    client = ArxivClient(transport=transport, clock=clock, sleeper=clock.sleep)
    with pytest.raises(ArxivFailure) as exc:
        client.search("ti:fixture", cancel_event=cancel)
    assert exc.value.category == "cancelled" and exc.value.execution["attempts"] == 0
    assert not transport.calls


def test_cancel_during_retry_wait_prevents_second_attempt():
    clock = Clock()
    cancel = threading.Event()
    transport = Sequence(clock, [HTTPResponse(500, b"", "application/xml")])

    def sleep(delay):
        clock.sleep(delay)
        cancel.set()

    client = ArxivClient(transport=transport, clock=clock, sleeper=sleep)
    with pytest.raises(ArxivFailure) as exc:
        client.search("ti:fixture", cancel_event=cancel)
    assert exc.value.category == "cancelled" and len(transport.calls) == 1


@pytest.mark.parametrize("value", [None, "bad", "-1", "1.5", "x" * 129])
def test_invalid_retry_after_is_not_interpreted_as_unbounded_wait(value):
    assert retry_after_seconds(value) is None


def test_retry_after_date_and_seconds():
    assert retry_after_seconds("12") == 12
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=10), usegmt=True)
    assert 8 <= retry_after_seconds(date) <= 10
    assert (
        retry_after_seconds(
            format_datetime(datetime(2020, 1, 1, tzinfo=UTC), usegmt=True)
        )
        == 0
    )


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429, 408])
def test_only_confirmed_transient_http_codes_are_retryable(status):
    assert retry_eligible("unavailable", f"upstream_http_{status}")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 422, 501, 505, 599])
def test_other_http_errors_are_not_automatically_retryable(status):
    assert not retry_eligible("unavailable", f"upstream_http_{status}")


def test_worker_environment_excludes_provider_and_database_secrets(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake-private-value")
    monkeypatch.setenv("H2_DATABASE_PASSWORD", "fake-database-value")
    env = IsolatedArxivTransport()._environment()
    assert "LLM_API_KEY" not in env and "H2_DATABASE_PASSWORD" not in env


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.invalid/api/query",
        "http://127.0.0.1:1234/other",
        "http://u:p@127.0.0.1:1234/api/query",
        "https://export.arxiv.org/api/query?extra=1",
    ],
)
def test_test_endpoint_is_not_an_arbitrary_url_configuration(endpoint):
    with pytest.raises(ValueError):
        IsolatedArxivTransport(endpoint=endpoint)


def test_unconfirmed_cleanup_is_fail_closed_and_cannot_start_another_process(
    monkeypatch,
):
    from app.papers import arxiv_transport

    created = []

    class StuckProcess:
        pid = 99999999
        returncode = None

        def __init__(self, *args, **kwargs):
            self.stdin, self.stdout, self.stderr = BytesIO(), BytesIO(), None
            created.append(self)

        def poll(self):
            return None

        def kill(self):
            pass

        def communicate(self, **kwargs):
            raise subprocess.TimeoutExpired("fake", 0)

    monkeypatch.setattr(arxiv_transport.subprocess, "Popen", StuckProcess)
    token = threading.Event()
    transport = IsolatedArxivTransport()
    # 已设置取消会在创建前退出；之后用短deadline验证不可回收模拟进程的拒绝路径。
    with pytest.raises(ArxivFailure) as exc:
        transport.attempt(
            "ti:fixture",
            1,
            deadline=arxiv_transport.time.monotonic() + 0.21,
            cancel_event=token,
        )
    assert exc.value.code == "arxiv_cleanup_unconfirmed"
    with pytest.raises(ArxivFailure) as second:
        transport.attempt(
            "ti:fixture", 1, deadline=arxiv_transport.time.monotonic() + 1
        )
    assert second.value.code == "arxiv_cleanup_unconfirmed" and len(created) == 1
