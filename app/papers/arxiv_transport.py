"""arXiv专用进程边界；不建立通用执行器或后台重试任务。"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from pathlib import Path

from app.papers.arxiv import ENDPOINT, ArxivFailure, ArxivSearchResult

POLL_SECONDS = 0.02
CLEANUP_RESERVE_SECONDS = 0.2
MAX_RESULT_BYTES = 2 * 1024 * 1024


def validated_endpoint(endpoint):
    parsed = urllib.parse.urlsplit(endpoint)
    if endpoint == ENDPOINT:
        return endpoint
    # 仅供Python故障夹具注入；模型Schema和环境变量没有URL配置入口。
    if (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "::1"}
        and parsed.port
        and parsed.path == "/api/query"
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    ):
        return endpoint
    raise ValueError("invalid_arxiv_transport_endpoint")


class IsolatedArxivTransport:
    def __init__(self, *, endpoint=ENDPOINT):
        self.endpoint = validated_endpoint(endpoint)
        self._command = [sys.executable, "-B", "-m", "app.papers.arxiv_attempt"]
        self._lock = threading.Lock()
        self._active = set()
        self._records = deque(maxlen=16)

    @property
    def active_pids(self):
        with self._lock:
            return tuple(self._active)

    @property
    def cleanup_records(self):
        with self._lock:
            return tuple(dict(item) for item in self._records)

    def _environment(self):
        names = {
            "PATH",
            "LANG",
            "LC_ALL",
            "SYSTEMROOT",
            "TMPDIR",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
        env = {k: v for k, v in os.environ.items() if k in names}
        if self.endpoint != ENDPOINT:
            env = {k: v for k, v in env.items() if "proxy" not in k.lower()}
            env["NO_PROXY"] = "*"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def attempt(self, query, top_k, *, deadline, cancel_event=None):
        with self._lock:
            if self._active:
                raise ArxivFailure("error", "arxiv_cleanup_unconfirmed")
        process = None
        reason = "completed"
        started = time.monotonic()
        active_deadline = deadline - CLEANUP_RESERVE_SECONDS
        if cancel_event is not None and cancel_event.is_set():
            raise ArxivFailure("cancelled", "arxiv_cancelled")
        if time.monotonic() >= active_deadline:
            raise ArxivFailure("timeout", "arxiv_timeout")
        request = json.dumps(
            {
                "endpoint": self.endpoint,
                "query": query,
                "top_k": top_k,
                "timeout": max(0.001, active_deadline - time.monotonic()),
            },
            ensure_ascii=False,
        ).encode()
        try:
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._environment(),
                cwd=Path(__file__).resolve().parents[2],
            )
            with self._lock:
                self._active.add(process.pid)
            first_input = request
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    reason = "cancelled"
                    raise ArxivFailure("cancelled", "arxiv_cancelled")
                remaining = active_deadline - time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                    raise ArxivFailure("timeout", "arxiv_timeout")
                try:
                    stdout, _ = process.communicate(
                        input=first_input, timeout=min(POLL_SECONDS, remaining)
                    )
                    break
                except subprocess.TimeoutExpired:
                    first_input = None
            if cancel_event is not None and cancel_event.is_set():
                reason = "cancelled"
                raise ArxivFailure("cancelled", "arxiv_cancelled")
            if time.monotonic() >= active_deadline:
                reason = "timeout"
                raise ArxivFailure("timeout", "arxiv_timeout")
            if process.returncode or len(stdout) > MAX_RESULT_BYTES:
                raise ArxivFailure("error", "arxiv_worker_failure")
            try:
                output = json.loads(stdout)
                if output["kind"] == "error":
                    category, code = output["category"], output["code"]
                    allowed_categories = {
                        "error",
                        "invalid_input",
                        "timeout",
                        "unavailable",
                    }
                    if (
                        category not in allowed_categories
                        or not isinstance(code, str)
                        or len(code) > 80
                    ):
                        raise ValueError("invalid_worker_error")
                    raise ArxivFailure(
                        category, code, retry_after=output.get("retry_after")
                    )
                if output["kind"] != "success":
                    raise ValueError("invalid_worker_output")
                result = ArxivSearchResult.model_validate(output["result"])
            except ArxivFailure:
                raise
            except (ValueError, TypeError, KeyError):
                raise ArxivFailure("error", "arxiv_worker_protocol_failure") from None
            if time.monotonic() >= active_deadline:
                reason = "timeout"
                raise ArxivFailure("timeout", "arxiv_timeout")
            return result
        except ArxivFailure:
            raise
        except (OSError, ValueError):
            reason = "process_failure"
            raise ArxivFailure("error", "arxiv_worker_failure") from None
        finally:
            if process is not None:
                reaped = process.poll() is not None
                try:
                    if not reaped:
                        process.kill()
                        process.communicate(
                            timeout=max(0.001, deadline - time.monotonic())
                        )
                        reaped = process.poll() is not None
                except (OSError, subprocess.TimeoutExpired):
                    reaped = process.poll() is not None
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            try:
                                stream.close()
                            except OSError:
                                pass
                    with self._lock:
                        if reaped:
                            self._active.discard(process.pid)
                        self._records.append(
                            {
                                "pid": process.pid,
                                "reaped": reaped,
                                "reason": reason,
                                "latency_ms": (time.monotonic() - started) * 1000,
                            }
                        )
                if not reaped:
                    raise ArxivFailure("error", "arxiv_cleanup_unconfirmed")
