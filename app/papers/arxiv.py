"""arXiv 公开元数据客户端；不下载正文、不生成 Evidence、不自动重试。"""

import hashlib
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, StrictStr

ENDPOINT = "https://export.arxiv.org/api/query"
REQUEST_INTERVAL_SECONDS = 3.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RESULTS = 10
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
IDENTITY = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*(?:\.[A-Z]{2})?/\d{7})(?:v[1-9]\d*)?\Z"
)


class ArxivFailure(Exception):
    """只携带稳定分类；不保留 URL、响应正文或底层异常文本。"""

    def __init__(self, category, code):
        self.category, self.code = category, code
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


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes
    content_type: str


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
        )


def _text(element, name):
    value = element.findtext(name)
    return " ".join(value.split()) if value else None


def parse_response(response, *, top_k):
    """先验证整个响应，再交付记录；坏条目不能被丢弃后伪装成 empty。"""
    if response.status != 200:
        category = "invalid_input" if response.status == 400 else "unavailable"
        raise ArxivFailure(category, f"upstream_http_{response.status}")
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


class ArxivClient:
    """每个应用运行时复用一个客户端，串行发送且间隔至少三秒。"""

    def __init__(
        self, *, timeout_seconds=15.0, transport=None, clock=None, sleeper=None
    ):
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 30
        ):
            raise ValueError("invalid_arxiv_timeout")
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport or _http_request
        self._clock, self._sleep = clock or time.monotonic, sleeper or time.sleep
        self._lock = threading.Lock()
        self._last_started = None

    def search(self, query, top_k=5):
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query.strip()) > 1000
            or any(ord(c) < 32 for c in query)
        ):
            raise ArxivFailure("invalid_input", "invalid_query")
        if type(top_k) is not int or not 1 <= top_k <= MAX_RESULTS:
            raise ArxivFailure("invalid_input", "invalid_top_k")
        deadline = self._clock() + self.timeout_seconds
        if not self._lock.acquire(timeout=self.timeout_seconds):
            raise ArxivFailure("timeout", "arxiv_timeout")
        try:
            if self._last_started is not None:
                delay = max(
                    0.0, REQUEST_INTERVAL_SECONDS - (self._clock() - self._last_started)
                )
                if self._clock() + delay >= deadline:
                    raise ArxivFailure("timeout", "arxiv_timeout")
                self._sleep(delay)
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise ArxivFailure("timeout", "arxiv_timeout")
            self._last_started = self._clock()
            parameters = urllib.parse.urlencode(
                {"search_query": query.strip(), "start": 0, "max_results": top_k}
            )
            try:
                response = self._transport(ENDPOINT + "?" + parameters, remaining)
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                raise ArxivFailure(
                    "invalid_input" if status == 400 else "unavailable",
                    f"upstream_http_{status}",
                ) from None
            except TimeoutError:
                raise ArxivFailure("timeout", "arxiv_timeout") from None
            except urllib.error.URLError as exc:
                category = (
                    "timeout" if isinstance(exc.reason, TimeoutError) else "unavailable"
                )
                raise ArxivFailure(
                    category,
                    "arxiv_timeout" if category == "timeout" else "network_unavailable",
                ) from None
            except OSError:
                raise ArxivFailure("unavailable", "network_unavailable") from None
            return parse_response(response, top_k=top_k)
        finally:
            self._lock.release()


class UnavailableArxivClient:
    def search(self, query, top_k=5):
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
