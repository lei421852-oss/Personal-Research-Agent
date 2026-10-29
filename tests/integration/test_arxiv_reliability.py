"""本地真实socket故障注入：无arXiv/LLM/PostgreSQL连接。"""

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.papers.arxiv import ArxivClient, ArxivFailure
from app.papers.arxiv_transport import IsolatedArxivTransport
from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.run_manager import MainAgentRunRequest
from app.tools.arxiv import SearchArxivTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from tests.integration.arxiv_fault_service import (
    fault_service,
    record_local_evidence,
    tls_stall_service,
)
from tests.test_react_workbench import (
    CATALOG,
    Dependencies,
    PaperSearch,
    factory,
    finish,
)
from tests.unit.papers.test_arxiv import feed


def setup(endpoint, timeout=6):
    transport = IsolatedArxivTransport(endpoint=endpoint)
    client = ArxivClient(timeout_seconds=timeout, transport=transport)
    registry = ToolRegistry()
    registry.register(SearchArxivTool(client))
    return ToolExecutor(registry), client, transport


def call(executor, **arguments):
    observation = executor.execute("search_arxiv", {"query": "ti:fixture", **arguments})
    transport = executor.registry.get("search_arxiv").client._transport
    record_local_evidence(
        "observation",
        {
            "observation": observation.model_dump(mode="json"),
            "trace": [trace.model_dump(mode="json") for trace in executor.traces],
            "worker_cleanup": transport.cleanup_records,
        },
    )
    return observation


def assert_reaped(transport):
    assert not transport.active_pids
    assert transport.cleanup_records and all(
        r["reaped"] for r in transport.cleanup_records
    )


def wait_until(predicate, timeout=0.8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


@pytest.mark.parametrize("empty", [False, True])
def test_local_success_and_empty_no_retry(empty):
    with fault_service([{"empty": empty}]) as server:
        ex, _, transport = setup(server.endpoint)
        observation = call(ex)
        assert observation.status == ("empty" if empty else "success")
        assert observation.payload["execution"]["attempts"] == 1
        assert len(server.records) == 1
        assert_reaped(transport)
        assert wait_until(lambda: server.active == 0)


def test_invalid_arguments_create_no_process_and_no_request():
    with fault_service([{}]) as server:
        ex, _, transport = setup(server.endpoint)
        observation = call(ex, top_k=0)
        assert observation.issue.code == "invalid_arguments"
        assert not server.records and not transport.cleanup_records


@pytest.mark.parametrize("mode", ["no_headers", "silent", "slow"])
def test_absolute_deadline_stops_read_and_releases_connection(mode):
    with fault_service([{"mode": mode}]) as server:
        ex, _, transport = setup(server.endpoint, timeout=1)
        started = time.monotonic()
        observation = call(ex)
        elapsed = time.monotonic() - started
        assert (
            observation.status == "error"
            and observation.payload["error_category"] == "timeout"
        )
        assert elapsed <= 1.2
        assert len(server.records) == 1
        assert_reaped(transport)
        assert wait_until(lambda: server.active == 0)


def test_tls_connection_establishment_is_inside_deadline():
    with tls_stall_service() as server:
        ex, _, transport = setup(server.endpoint, timeout=1)
        started = time.monotonic()
        observation = call(ex)
        assert observation.payload["error_category"] == "timeout"
        assert time.monotonic() - started <= 1.2
        assert server.accepted.is_set() and server.released.wait(0.8)
        assert_reaped(transport)


@pytest.mark.parametrize("first", [{"mode": "reset"}, {"status": 503}])
def test_transient_failure_then_success_has_one_retry(first):
    with fault_service([first, {}]) as server:
        ex, _, transport = setup(server.endpoint)
        observation = call(ex)
        assert observation.status == "success", observation.model_dump_json()
        assert observation.payload["execution"]["attempts"] == 2
        assert observation.payload["execution"]["automatic_retries"] == 1
        assert len(server.records) == 2
        assert server.records[1]["time"] - server.records[0]["time"] >= 3
        assert_reaped(transport)


def test_recoverable_errors_exhaust_two_attempts():
    with fault_service([{"status": 500}]) as server:
        ex, _, transport = setup(server.endpoint)
        observation = call(ex)
        assert (
            observation.status == "error"
            and observation.payload["error_code"] == "upstream_http_500"
        )
        assert observation.payload["execution"]["attempts"] == len(server.records) == 2
        assert observation.payload["execution"]["stop_reason"] == "attempt_limit"
        assert_reaped(transport)


@pytest.mark.parametrize("retry_after", ["1", "date"])
def test_429_obeys_interval_and_retry_after(retry_after):
    with fault_service([{"status": 429, "retry_after": retry_after}, {}]) as server:
        ex, _, transport = setup(server.endpoint, timeout=7)
        observation = call(ex)
        assert observation.status == "success", observation.model_dump_json()
        assert len(server.records) == 2
        assert server.records[1]["time"] - server.records[0]["time"] >= 3
        if retry_after == "date":
            assert server.records[1]["wall"] >= server.records[0]["retry_after_epoch"]
        assert_reaped(transport)


def test_retry_after_beyond_budget_does_not_retry_or_wait_forever():
    with fault_service([{"status": 429, "retry_after": "3600"}]) as server:
        ex, _, transport = setup(server.endpoint, timeout=1)
        started = time.monotonic()
        observation = call(ex)
        assert time.monotonic() - started < 1
        assert (
            observation.status == "error"
            and observation.payload["error_code"] == "upstream_http_429"
        )
        assert (
            observation.payload["execution"]["stop_reason"]
            == "insufficient_retry_budget"
        )
        assert len(server.records) == 1
        assert_reaped(transport)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 422, 501, 505])
def test_permanent_and_unclassified_http_errors_never_retry(status):
    with fault_service([{"status": status}]) as server:
        ex, _, transport = setup(server.endpoint)
        observation = call(ex)
        assert observation.status == "error" and not observation.issue.retryable
        assert observation.payload["execution"]["attempts"] == len(server.records) == 1
        assert_reaped(transport)


@pytest.mark.parametrize(
    "body",
    [
        b"not xml",
        feed(title="").body,
        feed(identity="../../private").body,
        b"x" * (1024 * 1024 + 1),
    ],
    ids=["invalid_xml", "missing_title", "forged_identity", "oversized_body"],
)
def test_invalid_responses_are_non_retryable_errors(body):
    with fault_service([{"body": body}]) as server:
        ex, _, transport = setup(server.endpoint)
        observation = call(ex)
        assert (
            observation.status == "error"
            and observation.issue.code == "internal_failure"
        )
        assert not observation.issue.retryable
        assert len(server.records) == 1
        assert_reaped(transport)


def test_cancel_active_request_reaps_process_without_retry():
    with fault_service([{"mode": "silent"}]) as server:
        _, client, transport = setup(server.endpoint)
        cancelled = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.search, "ti:fixture", cancel_event=cancelled)
            assert server.accepted.wait(2)
            started = time.monotonic()
            cancelled.set()
            with pytest.raises(ArxivFailure) as exc:
                pending.result(timeout=0.8)
        assert exc.value.category == "cancelled" and time.monotonic() - started < 0.8
        assert len(server.records) == 1
        assert_reaped(transport)
        assert wait_until(lambda: server.active == 0)


def test_queued_cancel_does_not_cancel_the_active_call():
    with fault_service([{"mode": "silent"}]) as server:
        _, client, transport = setup(server.endpoint)
        first_cancel, second_cancel = threading.Event(), threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.search, "ti:first", cancel_event=first_cancel)
            assert server.accepted.wait(2)
            second = pool.submit(client.search, "ti:second", cancel_event=second_cancel)
            second_cancel.set()
            with pytest.raises(ArxivFailure) as exc:
                second.result(timeout=0.6)
            assert (
                exc.value.category == "cancelled"
                and exc.value.execution["attempts"] == 0
            )
            assert not first.done()
            first_cancel.set()
            with pytest.raises(ArxivFailure):
                first.result(timeout=0.8)
        assert len(server.records) == 1
        assert_reaped(transport)


def test_concurrent_calls_remain_serial_and_spaced():
    with fault_service([{}]) as server:
        _, client, transport = setup(server.endpoint)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(client.search, "ti:fixture") for _ in range(2)]
            results = [p.result(timeout=7) for p in pending]
        assert all(len(r.papers) == 1 for r in results)
        assert len(server.records) == 2 and server.max_active == 1
        assert server.records[1]["time"] - server.records[0]["time"] >= 3
        assert_reaped(transport)


def test_late_response_cannot_overwrite_timeout():
    with fault_service([{"mode": "late"}]) as server:
        ex, _, transport = setup(server.endpoint, timeout=1)
        observation = call(ex)
        snapshot = observation.model_dump_json()
        assert observation.payload["error_category"] == "timeout"
        time.sleep(1)
        assert observation.model_dump_json() == snapshot and len(ex.traces) == 1
        assert_reaped(transport)


def test_blocked_processing_child_is_also_terminated():
    transport = IsolatedArxivTransport()
    # 控制进程模拟解析阶段停止前进；不修改生产解析器或使用真实服务。
    transport._command = [
        sys.executable,
        "-c",
        "import sys,time; sys.stdin.buffer.read(); time.sleep(30)",
    ]
    client = ArxivClient(timeout_seconds=1, transport=transport)
    started = time.monotonic()
    with pytest.raises(ArxivFailure) as exc:
        client.search("ti:fixture")
    assert exc.value.category == "timeout" and time.monotonic() - started <= 1.2
    assert_reaped(transport)


def test_main_runtime_cancellation_stops_inflight_arxiv_process():
    with fault_service([{"mode": "silent"}]) as server:
        _, client, transport = setup(server.endpoint)
        dependencies = Dependencies()
        llm, vision, _created, _calls, events = factory(
            dependencies,
            [
                ("search_arxiv", {"query": "ti:fixture"}),
                finish("insufficient", limitations=["Cancelled."]),
            ],
        )
        runtime = ReActWorkbenchRuntime(
            dependencies,
            llm_factory=llm,
            vision_factory=vision,
            paper_catalog=CATALOG,
            paper_retriever=PaperSearch(),
            arxiv_client=client,
        )

        async def scenario():
            task = asyncio.create_task(
                runtime.run(
                    MainAgentRunRequest(question="Find preprints.", max_model_calls=20),
                    lambda **e: events.append(e),
                )
            )
            until = time.monotonic() + 2
            while not server.accepted.is_set() and time.monotonic() < until:
                await asyncio.sleep(0.01)
            assert server.accepted.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as exc:
                await asyncio.wait_for(task, timeout=1)
            assert exc.value.evaluation_snapshot["status"] == "cancelled"

        asyncio.run(scenario())
        assert len(server.records) == 1
        assert not any(e["node_type"] == "Planner" for e in events)
        assert_reaped(transport)
