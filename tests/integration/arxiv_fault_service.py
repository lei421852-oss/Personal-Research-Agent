"""仅回环地址的有界故障服务，退出时关闭所有连接并回收服务线程。"""

import hashlib
import json
import os
import socket
import socketserver
import struct
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.unit.papers.test_arxiv import feed


def record_local_evidence(suffix, data):
    target = os.environ.get("B2_TEST_LOCAL_EVIDENCE_DIR")
    if not target:
        return
    case_id = os.environ.get("PYTEST_CURRENT_TEST", "manual-local-test")
    identity = hashlib.sha256(case_id.encode()).hexdigest()[:16]
    path = Path(target) / (identity + "-" + suffix + ".json")
    payload = {
        "kind": "controlled_local_fault_not_arxiv",
        "test_case": case_id[:500],
        **data,
    }
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class FaultServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, cases):
        self.cases = list(cases)
        self.records = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.accepted = threading.Event()
        self.connections = set()
        self.active = 0
        self.max_active = 0
        super().__init__(("127.0.0.1", 0), Handler)

    def handle_error(self, request, client_address):
        pass  # 故障注入的断连不是需要打印堆栈的服务异常。


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        with server.lock:
            index = len(server.records)
            case = server.cases[min(index, len(server.cases) - 1)]
            record = {"time": time.monotonic(), "wall": time.time(), "released": False}
            server.records.append(record)
            server.connections.add(self.connection)
            server.active += 1
            server.max_active = max(server.max_active, server.active)
        server.accepted.set()
        try:
            mode = case.get("mode")
            if mode == "reset":
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                self.connection.close()
                return
            if mode == "no_headers":
                self.connection.settimeout(0.05)
                while not server.stop.is_set():
                    try:
                        if not self.connection.recv(1024):
                            return
                    except TimeoutError:
                        pass
                return
            body = case.get("body", feed(empty=case.get("empty", False)).body)
            status = case.get("status", 200)
            self.send_response(status)
            self.send_header("Content-Type", "application/atom+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            retry = case.get("retry_after")
            if retry == "date":
                target = datetime.now(UTC) + timedelta(seconds=4)
                retry = format_datetime(target, usegmt=True)
                record["retry_after_epoch"] = target.replace(microsecond=0).timestamp()
            if retry is not None:
                self.send_header("Retry-After", str(retry))
            self.end_headers()
            self.wfile.flush()
            if mode == "silent":
                self.connection.settimeout(0.05)
                while not server.stop.is_set():
                    try:
                        if not self.connection.recv(1024):
                            return
                    except TimeoutError:
                        pass
                return
            if mode == "late" and server.stop.wait(1.5):
                return
            if mode == "slow":
                for value in body:
                    if server.stop.wait(0.025):
                        return
                    self.wfile.write(bytes([value]))
                    self.wfile.flush()
            else:
                self.wfile.write(body)
                self.wfile.flush()
        except OSError:
            pass
        finally:
            self.close_connection = True
            with server.lock:
                server.connections.discard(self.connection)
                server.active -= 1
                record["released"] = True


@contextmanager
def fault_service(cases):
    server = FaultServer(cases)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.02),
        name="arxiv-local-fault-service",
    )
    thread.start()
    server.endpoint = f"http://127.0.0.1:{server.server_port}/api/query"
    try:
        yield server
    finally:
        server.stop.set()
        server.shutdown()
        for connection in tuple(server.connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass
        server.server_close()
        thread.join(timeout=1)
        assert not thread.is_alive()
        record_local_evidence(
            "server",
            {
                "http_requests": len(server.records),
                "records": server.records,
                "max_active_connections": server.max_active,
                "active_connections_after_cleanup": server.active,
                "service_thread_stopped": not thread.is_alive(),
            },
        )


class TLSStallHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.accepted.set()
        self.request.settimeout(0.05)
        try:
            while not self.server.stop.is_set():
                try:
                    if not self.request.recv(4096):
                        break
                except TimeoutError:
                    pass
        except OSError:
            pass
        finally:
            self.server.released.set()


@contextmanager
def tls_stall_service():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TLSStallHandler)
    server.accepted, server.released, server.stop = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.02),
        name="arxiv-tls-stall-service",
    )
    thread.start()
    server.endpoint = f"https://127.0.0.1:{server.server_address[1]}/api/query"
    try:
        yield server
    finally:
        server.stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        assert not thread.is_alive()
        record_local_evidence(
            "tls-server",
            {
                "tcp_accepted": server.accepted.is_set(),
                "tcp_released": server.released.is_set(),
                "http_requests": 0,
                "service_thread_stopped": not thread.is_alive(),
            },
        )
