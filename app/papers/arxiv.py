"""arXiv 公开元数据客户端；不下载正文、不生成 Evidence、最多一次有界传输重试。"""

import hashlib
import http.client
import math
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from pydantic import BaseModel, ConfigDict, Field, StrictStr

ENDPOINT = "https://export.arxiv.org/api/query"
REQUEST_INTERVAL_SECONDS = 3.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RESULTS = 10
MAX_ATTEMPTS = 2
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
IDENTITY = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*(?:\.[A-Z]{2})?/\d{7})(?:v[1-9]\d*)?\Z"
)


class ArxivFailure(Exception):
    """只携带稳定分类；不保留 URL、响应正文或底层异常文本。"""

    def __init__(self, category, code, *, retry_after=None, execution=None):
        self.category, self.code = category, code
        self.retry_after = retry_after
        self.execution = execution or {}
        super().__init__(code)


class ArxivPaper(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arxiv_id: StrictStr
    title: StrictStr = Field(min_length=1, max_length=2000)
    source_url: StrictStr
    abstract: StrictStr | None = Field(default=None, max_length=4000)
    abstract_truncated: bool = False
    authors: list[StrictStr] = Field(max_length=200)
    published: StrictStr
    year: int
    categories: list[StrictStr] = Field(max_length=100)
    doi: StrictStr | None = Field(default=None, max_length=500)
    journal_reference: StrictStr | None = Field(default=None, max_length=2000)


class ArxivSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    papers: list[ArxivPaper] = Field(max_length=MAX_RESULTS)
    response_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: StrictStr
    execution: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes
    content_type: str
    retry_after: str | None = None


def arxiv_identity(url):
    """只解析官方摘要页的原始标识符；版本不同不会被偷偷合并。"""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc != "arxiv.org"
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/abs/")
    ):
        raise ValueError("invalid_arxiv_source")
    identity = parsed.path.removeprefix("/abs/")
    if not IDENTITY.fullmatch(identity):
        raise ValueError("invalid_arxiv_identity")
    return identity


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _http_request(url, timeout):
    # 端点由本模块固定；不跟随上游跳转，不读取用户 .env 或发送鉴权值。
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "personal-agent-arxiv-tool/1.0",
            "Accept": "application/atom+xml",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return HTTPResponse(
            response.status,
            response.read(MAX_RESPONSE_BYTES + 1),
            response.headers.get("Content-Type", ""),
            response.headers.get("Retry-After"),
        )


def _text(element, name):
    value = element.findtext(name)
    return " ".join(value.split()) if value else None


def parse_response(response, *, top_k):
    """先验证整个响应，再交付记录；坏条目不能被丢弃后伪装成 empty。"""
    if response.status != 200:
        category = "invalid_input" if response.status == 400 else "unavailable"
        raise ArxivFailure(
            category,
            f"upstream_http_{response.status}",
            retry_after=retry_after_seconds(response.retry_after),
        )
    body = response.body
    if len(body) > MAX_RESPONSE_BYTES:
        raise ArxivFailure("error", "response_too_large")
    if (
        "xml" not in response.content_type.lower()
        or b"\x00" in body
        or b"<!DOCTYPE" in body.upper()
        or b"<!ENTITY" in body.upper()
    ):
        raise ArxivFailure("error", "invalid_upstream_response")
    try:
        feed = ET.fromstring(body)
        if feed.tag != ATOM + "feed":
            raise ValueError("not_atom_feed")
        papers, seen = [], set()
        entries = feed.findall(ATOM + "entry")
        total = int(
            feed.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults", "")
        )
        if total < 0 or (not entries and total != 0) or (entries and total == 0):
            raise ValueError("invalid_result_count")
        if len(entries) > MAX_RESULTS:
            raise ValueError("too_many_entries")
        for entry in entries:
            original_id = _text(entry, ATOM + "id") or ""
            if original_id.startswith(
                ("http://arxiv.org/api/errors", "https://arxiv.org/api/errors")
            ):
                raise ArxivFailure("invalid_input", "upstream_query_error")
            identity = arxiv_identity(original_id)
            title = _text(entry, ATOM + "title")
            published = _text(entry, ATOM + "published")
            date = datetime.fromisoformat(published or "")
            if date.tzinfo is None:
                raise ValueError("missing_source_timezone")
            authors = [
                _text(author, ATOM + "name")
                for author in entry.findall(ATOM + "author")
            ]
            if not authors or any(not author for author in authors):
                raise ValueError("missing_source_author")
            abstract = _text(entry, ATOM + "summary")
            paper = ArxivPaper(
                arxiv_id=identity,
                title=title,
                source_url="https://arxiv.org/abs/" + identity,
                abstract=abstract[:4000] if abstract else None,
                abstract_truncated=bool(abstract and len(abstract) > 4000),
                authors=authors,
                published=published,
                year=date.year,
                categories=list(
                    dict.fromkeys(
                        category.attrib["term"]
                        for category in entry.findall(ATOM + "category")
                        if category.attrib.get("term")
                    )
                ),
                doi=_text(entry, ARXIV + "doi"),
                journal_reference=_text(entry, ARXIV + "journal_ref"),
            )
            if identity not in seen:
                papers.append(paper)
                seen.add(identity)
        return ArxivSearchResult(
            papers=papers[:top_k],
            response_sha256=hashlib.sha256(body).hexdigest(),
            retrieved_at=datetime.now(UTC).isoformat(),
        )
    except ArxivFailure:
        raise
    except (ET.ParseError, ValueError, TypeError, KeyError):
        raise ArxivFailure("error", "invalid_upstream_response") from None


def retry_after_seconds(value):
    """只接收RFC允许的秒数或HTTP日期；未知值不创造等待要求。"""
    if not isinstance(value, str) or len(value) > 128:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0.0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def retry_eligible(category, code):
    return (category == "timeout" and code == "arxiv_timeout") or (
        category == "unavailable"
        and code
        in {
            "network_unavailable",
            "upstream_http_408",
            "upstream_http_429",
            "upstream_http_500",
            "upstream_http_502",
            "upstream_http_503",
            "upstream_http_504",
        }
    )


def network_failure(exc):
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        delay = (
            retry_after_seconds(exc.headers.get("Retry-After")) if exc.headers else None
        )
        exc.close()
        return ArxivFailure(
            "invalid_input" if status == 400 else "unavailable",
            f"upstream_http_{status}",
            retry_after=delay,
        )
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ArxivFailure("unavailable", "certificate_validation_failed")
    if isinstance(reason, TimeoutError):
        return ArxivFailure("timeout", "arxiv_timeout")
    if isinstance(exc, (OSError, urllib.error.URLError, http.client.HTTPException)):
        return ArxivFailure("unavailable", "network_unavailable")
    return ArxivFailure("error", "invalid_transport_result")


class ArxivClient:
    """一个deadline覆盖排队、两次尝试、等待与解析；默认传输可终止回收。"""

    def __init__(
        self, *, timeout_seconds=15.0, transport=None, clock=None, sleeper=None
    ):
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 30
        ):
            raise ValueError("invalid_arxiv_timeout")
        from app.papers.arxiv_transport import IsolatedArxivTransport

        self.timeout_seconds = float(timeout_seconds)
        self._transport = (
            transport if transport is not None else IsolatedArxivTransport()
        )
        self._clock, self._sleep = clock or time.monotonic, sleeper or time.sleep
        self._custom_sleep = sleeper is not None
        self._lock = threading.Lock()
        self._next_start = None

    def _check(self, deadline, cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise ArxivFailure("cancelled", "arxiv_cancelled")
        if self._clock() >= deadline:
            raise ArxivFailure("timeout", "arxiv_timeout")

    def _wait(self, delay, deadline, cancel_event):
        self._check(deadline, cancel_event)
        if delay > 0:
            if cancel_event is None or self._custom_sleep:
                self._sleep(delay)
            elif cancel_event.wait(delay):
                raise ArxivFailure("cancelled", "arxiv_cancelled")
        self._check(deadline, cancel_event)

    def search(self, query, top_k=5, *, cancel_event=None):
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query.strip()) > 1000
            or any(ord(c) < 32 or ord(c) == 127 for c in query)
        ):
            raise ArxivFailure("invalid_input", "invalid_query")
        if type(top_k) is not int or not 1 <= top_k <= MAX_RESULTS:
            raise ArxivFailure("invalid_input", "invalid_top_k")
        started = self._clock()
        deadline = started + self.timeout_seconds
        attempts = []
        acquired = False
        stop_reason = "completed"
        last_failure = None

        def metadata():
            return {
                "attempts": len(attempts),
                "automatic_retries": max(0, len(attempts) - 1),
                "elapsed_ms": max(0.0, (self._clock() - started) * 1000),
                "stop_reason": stop_reason,
                "attempt_log": list(attempts),
            }

        try:
            while not acquired:
                self._check(deadline, cancel_event)
                acquired = self._lock.acquire(
                    timeout=max(0.0, min(0.02, deadline - self._clock()))
                )
            from app.papers.arxiv_transport import CLEANUP_RESERVE_SECONDS

            retry_not_before = started
            for number in range(1, MAX_ATTEMPTS + 1):
                self._check(deadline, cancel_event)
                delay = max(
                    0.0,
                    (self._next_start or started) - self._clock(),
                    retry_not_before - self._clock(),
                )
                if self._clock() + delay + CLEANUP_RESERVE_SECONDS >= deadline:
                    stop_reason = (
                        "insufficient_retry_budget"
                        if last_failure
                        else "deadline_before_attempt"
                    )
                    raise last_failure or ArxivFailure("timeout", "arxiv_timeout")
                self._wait(delay, deadline, cancel_event)
                attempt_started = self._clock()
                record = {"attempt": number}
                attempts.append(record)
                try:
                    from app.papers.arxiv_transport import IsolatedArxivTransport

                    if isinstance(self._transport, IsolatedArxivTransport):
                        result = self._transport.attempt(
                            query.strip(),
                            top_k,
                            deadline=deadline,
                            cancel_event=cancel_event,
                        )
                    else:
                        # 旧的内存Fake注入仅用于确定性测试；真实网络必须走隔离传输。
                        parameters = urllib.parse.urlencode(
                            {
                                "search_query": query.strip(),
                                "start": 0,
                                "max_results": top_k,
                            }
                        )
                        response = self._transport(
                            ENDPOINT + "?" + parameters, deadline - self._clock()
                        )
                        result = parse_response(response, top_k=top_k)
                    self._check(deadline, cancel_event)
                    record.update(
                        outcome="success" if result.papers else "empty",
                        latency_ms=max(0.0, (self._clock() - attempt_started) * 1000),
                    )
                    return result.model_copy(update={"execution": metadata()})
                except Exception as exc:  # noqa: BLE001 - 仅在arXiv边界归一化，不跨工具重试
                    failure = (
                        exc if isinstance(exc, ArxivFailure) else network_failure(exc)
                    )
                    record.update(
                        outcome="error",
                        error_category=failure.category,
                        error_code=failure.code,
                        retry_after_seconds=failure.retry_after,
                        latency_ms=max(0.0, (self._clock() - attempt_started) * 1000),
                    )
                    last_failure = failure
                    if not retry_eligible(failure.category, failure.code):
                        stop_reason = "non_retryable"
                        raise failure from None
                    if number == MAX_ATTEMPTS:
                        stop_reason = "attempt_limit"
                        raise failure from None
                    retry_not_before = self._clock() + (failure.retry_after or 0)
                finally:
                    # 从完成点算三秒更保守，跨子进程启动抖动仍满足起始间隔。
                    self._next_start = self._clock() + REQUEST_INTERVAL_SECONDS
            raise ArxivFailure("error", "invalid_transport_result")
        except ArxivFailure as exc:
            if exc.category in {"cancelled", "timeout"}:
                stop_reason = exc.category
            exc.execution = metadata()
            raise
        finally:
            if acquired:
                self._lock.release()


class UnavailableArxivClient:
    def search(self, query, top_k=5, *, cancel_event=None):
        raise ArxivFailure("unavailable", "configuration_invalid")


def configured_arxiv_client(environment):
    """配置按运行时快照读取；禁用时忽略无关配置，错误配置不会阻断应用启动。"""
    enabled = str(environment.get("AGENT_ARXIV_ENABLED", "false")).strip().lower()
    if enabled in {"false", "no", "0", ""}:
        return None
    if enabled not in {"true", "yes", "1"}:
        return UnavailableArxivClient()
    try:
        return ArxivClient(
            timeout_seconds=float(environment.get("AGENT_ARXIV_TIMEOUT_SECONDS", "15"))
        )
    except (TypeError, ValueError):
        return UnavailableArxivClient()
