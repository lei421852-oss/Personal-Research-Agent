"""首次持久化故障的结构化诊断；仅使用离线替身。"""

import asyncio
import json
import subprocess
import sys

import pytest

from app.persistence.coordinator import PersistenceLatch
from app.persistence.errors import StorageError
from app.persistence.failure_diagnostics import build_failure, safe_database_metadata
from app.persistence.repository import ConflictError
from app.persistence.writer import RunWriter, WriterFailed
from tests.unit.persistence.test_writer import FakeRepository, append


def test_sqlstate_strict_whitelist():
    class Original:
        sqlstate = "23505"

    class DriverError(Exception):
        orig = Original()

    assert safe_database_metadata(DriverError()) == "23505"
    Original.sqlstate = "secret://password"
    assert safe_database_metadata(DriverError()) is None


def test_failure_is_whitelisted_and_no_sensitive_text():
    error = ConflictError("event_cursor_conflict", detail="password secret sk-private")
    event = build_failure(
        run_id="a" * 32,
        operation="append_events",
        stage="writer_apply",
        event_type="progress",
        exc=error,
        reason="secret",
    )
    assert event["internal_error_code"] == "event_cursor_conflict"
    assert event["category"] == "event_cursor_conflict"
    assert event["event_type"] == "progress"
    assert "secret" not in json.dumps(event)
    assert "password" not in json.dumps(event)
    assert "sk-private" not in json.dumps(event)


def test_shared_latch_only_emits_and_retains_first_failure(monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.persistence.writer.emit_failure", lambda event: events.append(event)
    )

    async def scenario():
        latch = PersistenceLatch()
        repo = FakeRepository()
        repo.fail_times = 1
        repo.error = ConflictError("event_cursor_conflict", detail="sk-private")
        first = RunWriter(repo, latch=latch)
        second = RunWriter(repo, latch=latch)
        first.start()
        try:
            first.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await first.flush()
            second._trip("writer_queue_full", command=append(2, 0), stage="queue")
            assert latch.reason == "writer_write_failed:event_cursor_conflict"
            assert latch.diagnostic == events[0]
            assert len(events) == 1
            assert latch.diagnostic["run_id"] == "a" * 32
        finally:
            await first.close()

    asyncio.run(scenario())


def test_emit_failure_exception_does_not_replace_persistence_error(monkeypatch):
    def broken(_):
        raise RuntimeError("logger failed")

    monkeypatch.setattr("app.persistence.writer.emit_failure", broken)

    async def scenario():
        repo = FakeRepository()
        repo.fail_times = 1
        repo.error = ConflictError("event_cursor_conflict")
        latch = PersistenceLatch()
        writer = RunWriter(repo, latch=latch)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert latch.reason == "writer_write_failed:event_cursor_conflict"
        finally:
            await writer.close()

    asyncio.run(scenario())


def test_subprocess_normal_exit_emits_safe_record():
    script = """
import logging
from app.persistence.failure_diagnostics import build_failure, emit_failure, flush_diagnostics
from app.persistence.repository import ConflictError
logging.basicConfig(level=logging.ERROR)
event=build_failure(run_id='a'*32,operation='append_events',stage='writer_apply',event_type='started',exc=ConflictError('event_cursor_conflict',detail='sk-private'),reason='private')
assert emit_failure(event)
assert flush_diagnostics(2)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert "persistence.first_failure" in result.stderr
    assert "event_cursor_conflict" in result.stderr
    assert "sk-private" not in result.stderr


def test_diagnostic_builder_failure_cannot_prevent_latch(monkeypatch):
    def broken(**_):
        raise ValueError("untrusted")

    monkeypatch.setattr("app.persistence.writer.build_failure", broken)
    latch = PersistenceLatch()
    writer = RunWriter(FakeRepository(), latch=latch)
    writer._trip("writer_queue_full", command=append(1, 0), stage="queue")
    assert latch.reason == "writer_queue_full"
    assert latch.diagnostic is None


def test_slow_log_handler_does_not_block_submit():
    script = """
import threading, time
from app.persistence import failure_diagnostics as d
gate = threading.Event()
started = threading.Event()
def slow(*args, **kwargs):
    started.set()
    gate.wait(2)
d.LOGGER.error = slow
assert d.emit_failure({'event':'persistence.first_failure'})
assert started.wait(1)
before=time.monotonic()
assert d.emit_failure({'event':'persistence.first_failure'})
assert time.monotonic()-before < 0.25
gate.set()
assert d.flush_diagnostics(2)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0


def test_nonzero_exit_flushes_safe_record():
    script = """
import logging, sys
from app.persistence.failure_diagnostics import build_failure, emit_failure
from app.persistence.errors import StorageError
logging.basicConfig(level=logging.ERROR)
emit_failure(build_failure(run_id='b'*32,operation='append_events',stage='writer_apply',event_type='started',exc=StorageError('storage_pool_timeout'),reason='x'))
sys.exit(7)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 7
    assert "persistence.first_failure" in result.stderr
    assert "database_timeout" in result.stderr


def test_exhausted_retries_keep_first_diagnostic_and_final_latch_reason(monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.persistence.writer.emit_failure", lambda event: events.append(event)
    )

    class TwoFailures(FakeRepository):
        async def append_events(self, *args, **kwargs):
            self.fail_times += 1
            if self.fail_times == 1:
                raise StorageError(
                    "storage_pool_timeout", retryable=True, replay_safe=True
                )
            raise StorageError("storage_unavailable", retryable=True)

    async def scenario():
        latch = PersistenceLatch()
        writer = RunWriter(TwoFailures(), latch=latch)
        writer.start()
        try:
            writer.submit(append(1, 0))
            with pytest.raises(WriterFailed):
                await writer.flush()
            assert latch.reason == "writer_write_failed:storage_unavailable"
            assert latch.diagnostic["internal_error_code"] == "storage_pool_timeout"
            assert latch.diagnostic["category"] == "database_timeout"
            assert [event["event"] for event in events] == [
                "persistence.write_retry",
                "persistence.first_failure",
            ]
        finally:
            await writer.close()

    asyncio.run(scenario())
