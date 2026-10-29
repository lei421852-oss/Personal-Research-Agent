"""受信任本地stdio客户端：单请求进程、有界管道、统一截止时间和显式回收。"""

import json
import math
import os
import selectors
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from app.tools.integrations.error_mapper import ERRORS, ExternalToolError

POLL_SECONDS = 0.01
CLEANUP_RESERVE_SECONDS = 0.2
ENVIRONMENT_ALLOWLIST = {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _check_json(value, limit, depth=0, remaining=None):
    """编码前限制深度和现存字符串，避免先构造超大中间输出。"""
    remaining = [limit] if remaining is None else remaining
    remaining[0] -= 1 + (len(value) if type(value) is str else 0)
    if remaining[0] < 0 or depth > 8:
        raise ValueError("json_depth")
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is str and len(value) <= limit:
        return
    if type(value) is list and len(value) <= limit:
        for item in value:
            _check_json(item, limit, depth + 1, remaining)
        return
    if type(value) is dict and len(value) <= limit:
        for key, item in value.items():
            if type(key) is not str or len(key) > limit:
                raise ValueError("json_key")
            remaining[0] -= len(key)
            _check_json(item, limit, depth + 1, remaining)
        return
    raise ValueError("invalid_json_value")


def _encode_bounded(value, limit):
    try:
        _check_json(value, limit)
        output = bytearray()
        for part in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(
            value
        ):
            encoded = part.encode("utf-8")
            if len(output) + len(encoded) > limit:
                raise ValueError("json_limit")
            output.extend(encoded)
        return bytes(output)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ExternalToolError("input_limit") from None


class StdioToolClient:
    """命令仅来自管理员配置；不接受模型、工具描述或返回值提供的命令。"""

    def __init__(self, command, config):
        if (
            type(command) is not tuple
            or not command
            or len(command) > 16
            or any(type(x) is not str or not x or len(x) > 512 for x in command)
            or not Path(command[0]).is_absolute()
        ):
            raise ValueError("invalid_external_tool_command")
        self.command, self.config = command, config
        self._closed = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._call_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = set()
        self._poisoned = False
        self.records = deque(maxlen=32)

    @property
    def active_pids(self):
        with self._state_lock:
            return tuple(self._active)

    def _check(self, deadline, cancel_event):
        if self._closed.is_set():
            raise ExternalToolError("closed")
        if cancel_event is not None and cancel_event.is_set():
            raise ExternalToolError("cancelled")
        if time.monotonic() >= deadline:
            raise ExternalToolError("timeout")

    def _environment(self):
        # 不继承Provider/数据库凭据；本地命令受信任，这不等于OS级文件访问沙箱。
        result = {k: v for k, v in os.environ.items() if k in ENVIRONMENT_ALLOWLIST}
        result.update(
            PYTHONPATH=str(Path(__file__).resolve().parents[3]),
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        return result

    @staticmethod
    def _cleanup_process(process, deadline):
        # 独立进程组便于停止本次进程的普通子进程；不提供恶意程序隔离保证。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return process.poll() is not None

    def request(
        self, operation, *, name=None, arguments=None, cancel_event=None, deadline=None
    ):
        started = time.monotonic()
        deadline = min(
            deadline if deadline is not None else float("inf"),
            started + self.config.timeout_seconds,
        )
        reserve = min(CLEANUP_RESERVE_SECONDS, self.config.timeout_seconds / 4)
        active_deadline = deadline - reserve
        request_id = uuid4().hex
        request = {"version": 1, "request_id": request_id, "operation": operation}
        if operation == "call_tool":
            request.update(name=name, arguments=arguments)
        metadata = {
            "request_id": request_id,
            "operation": operation,
            "attempts": 0,
            "output_bytes": 0,
            "automatic_retries": 0,
        }
        acquired, process, selector = False, None, None
        failure, response = None, None
        try:
            if os.name != "posix":
                raise ExternalToolError("unavailable")
            while not acquired:
                self._check(active_deadline, cancel_event)
                acquired = self._call_lock.acquire(timeout=POLL_SECONDS)
            self._idle.clear()
            with self._state_lock:
                if self._poisoned or self._active:
                    raise ExternalToolError("cleanup_failed")
            self._check(active_deadline, cancel_event)
            encoded = _encode_bounded(request, self.config.max_input_bytes)
            self._check(active_deadline, cancel_event)
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._environment(),
                start_new_session=True,
            )
            metadata["attempts"] = 1
            with self._state_lock:
                self._active.add(process.pid)
            selector = selectors.DefaultSelector()
            for stream in (process.stdin, process.stdout):
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            output, sent, eof = bytearray(), 0, False
            while not eof or process.poll() is None:
                self._check(active_deadline, cancel_event)
                for key, _ in selector.select(
                    timeout=min(
                        POLL_SECONDS, max(0.0, active_deadline - time.monotonic())
                    )
                ):
                    if key.fileobj is process.stdin:
                        try:
                            sent += os.write(
                                process.stdin.fileno(), encoded[sent : sent + 8192]
                            )
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            raise ExternalToolError("invalid_output") from None
                        if sent == len(encoded):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(
                            process.stdout.fileno(),
                            min(8192, self.config.max_output_bytes + 1 - len(output)),
                        )
                        if not chunk:
                            eof = True
                            selector.unregister(process.stdout)
                        else:
                            output.extend(chunk)
                            metadata["output_bytes"] = len(output)
                            if len(output) > self.config.max_output_bytes:
                                raise ExternalToolError("output_limit")
            self._check(active_deadline, cancel_event)
            if process.returncode != 0:
                raise ExternalToolError("unavailable")
            try:
                response = json.loads(
                    output,
                    object_pairs_hook=_unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                _check_json(response, self.config.max_output_bytes)
            except (ValueError, UnicodeError, RecursionError):
                raise ExternalToolError("invalid_output") from None
            if (
                type(response) is not dict
                or type(response.get("version")) is not int
                or response["version"] != 1
                or response.get("request_id") != request_id
                or type(response.get("ok")) is not bool
            ):
                raise ExternalToolError("invalid_output")
            if not response["ok"]:
                if set(response) != {"version", "request_id", "ok", "error_code"}:
                    raise ExternalToolError("invalid_output")
                code = response["error_code"]
                if type(code) is not str or code not in ERRORS:
                    raise ExternalToolError("invalid_output")
                raise ExternalToolError(code)
            key = "tools" if operation == "list_tools" else "result"
            if set(response) != {"version", "request_id", "ok", key}:
                raise ExternalToolError("invalid_output")
            self._check(active_deadline, cancel_event)
            response = response[key]
        except ExternalToolError as exc:
            failure = exc
        except OSError:
            failure = ExternalToolError("unavailable")
        except Exception:  # noqa: BLE001 - 未知异常不回显命令、输出或堆栈
            failure = ExternalToolError("internal_failure")
        finally:
            if selector is not None:
                selector.close()
            if process is not None:
                reaped = False
                try:
                    reaped = self._cleanup_process(process, deadline)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    for stream in (process.stdin, process.stdout):
                        if stream is not None:
                            stream.close()
                metadata["cleanup"] = {
                    "pid": process.pid,
                    "reaped": reaped,
                    "returncode": process.returncode,
                }
                with self._state_lock:
                    if reaped:
                        self._active.discard(process.pid)
                    else:
                        self._poisoned = True
                if not reaped:
                    failure = ExternalToolError("cleanup_failed")
            metadata.update(
                elapsed_ms=(time.monotonic() - started) * 1000,
                stop_reason=failure.code if failure else "completed",
            )
            with self._state_lock:
                self.records.append(dict(metadata))
            if acquired:
                self._idle.set()
                self._call_lock.release()
        if failure:
            failure.execution = metadata
            raise failure
        return response, metadata

    def close(self):
        self._closed.set()
        if not self._idle.wait(timeout=CLEANUP_RESERVE_SECONDS + 0.1):
            raise ExternalToolError("cleanup_failed")
        if self.active_pids:
            raise ExternalToolError("cleanup_failed")
