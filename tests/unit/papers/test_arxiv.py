"""外部服务均由固定HTTP响应模拟；不代表真实arXiv验收。"""

import hashlib
import json
import urllib.parse
from html import escape

import pytest
from pydantic import ValidationError

from app.papers.arxiv import (
    MAX_RESPONSE_BYTES,
    ArxivClient,
    ArxivFailure,
    HTTPResponse,
    arxiv_identity,
    configured_arxiv_client,
    parse_response,
)
from app.papers.catalog import load_paper_catalog
from app.tools.arxiv import SearchArxivInput, SearchArxivTool
from app.tools.executor import ToolExecutor
from app.tools.observations import ToolObservation
from app.tools.registry import ToolRegistry

QUERY = 'ti:"vision language"'
IDENTITY = "2401.12345v1"


def feed(
    *,
    identity=IDENTITY,
    title="Fixture preprint",
    summary="Fixture abstract.",
    author="Fixture Author",
    published="2024-01-01T00:00:00Z",
    empty=False,
):
    entry = (
        ""
        if empty
        else (
            f"<entry><id>http://arxiv.org/abs/{escape(identity)}</id>"
            f"<title>{escape(title)}</title><summary>{escape(summary)}</summary>"
            f"<published>{published}</published><author><name>{escape(author)}</name></author>"
            '<category term="cs.CV"/></entry>'
        )
    )
    return HTTPResponse(
        200,
        (
            '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
            + f"<o:totalResults>{0 if empty else 1}</o:totalResults>"
            + entry
            + "</feed>"
        ).encode(),
        "application/atom+xml",
    )


class Clock:
    def __init__(self):
        self.now, self.delays = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


class Transport:
    def __init__(self, response=None, error=None):
        self.response, self.error = response or feed(), error
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return self.response


def client(transport=None):
    clock = Clock()
    return ArxivClient(
        transport=transport or Transport(), clock=clock, sleeper=clock.sleep
    )


def executor(transport=None, **kwargs):
    registry = ToolRegistry()
    tool = SearchArxivTool(client(transport), **kwargs)
    registry.register(tool)
    return ToolExecutor(registry), tool


def test_registration_schema_and_success_round_trip():
    ex, tool = executor()
    schema = tool.schema()
    assert schema["name"] == "search_arxiv"
    assert schema["input_schema"]["additionalProperties"] is False
    assert schema["input_schema"]["required"] == ["query"]
    with pytest.raises(ValueError):
        ex.registry.register(tool)
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert ToolObservation.model_validate_json(result.model_dump_json()) == result
    assert result.status == "success" and result.result_count == 1
    paper = result.payload["papers"][0]
    assert paper["paper_id"] == "arxiv:" + IDENTITY
    assert paper["source_url"] == "https://arxiv.org/abs/" + IDENTITY
    assert paper["title"] == "Fixture preprint" and paper["year"] == 2024
    assert not paper["paper_evidence_available"]
    assert "evidence_id" not in paper and "score" not in paper
    assert ex.traces[0].retrieved_paper_ids == ["arxiv:" + IDENTITY]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": " "},
        {"query": 1},
        {"query": True},
        {"query": "x", "paper_id": "anything"},
        {"query": "x", "top_k": True},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": 11},
        {"query": "x", "top_k": "2"},
        {"query": "x" * 1001},
        {"query": "x\ny"},
        {"query": "x\x7fy"},
    ],
)
def test_invalid_arguments_never_reach_transport(arguments):
    transport = Transport()
    ex, _ = executor(transport)
    result = ex.execute("search_arxiv", arguments)
    assert result.status == "error" and result.issue.code == "invalid_arguments"
    assert transport.calls == []


def test_trimming_and_parameter_encoding_does_not_change_endpoint():
    transport = Transport()
    ex, _ = executor(transport)
    result = ex.execute(
        "search_arxiv",
        {"query": "  " + QUERY + " & url=https://evil.invalid  ", "top_k": 2},
    )
    assert result.status == "success"
    parsed = urllib.parse.urlsplit(transport.calls[0][0])
    assert (
        parsed.scheme == "https"
        and parsed.netloc == "export.arxiv.org"
        and parsed.path == "/api/query"
    )
    assert urllib.parse.parse_qs(parsed.query)["search_query"] == [
        QUERY + " & url=https://evil.invalid"
    ]
    assert 0 < transport.calls[0][1] <= 15


def test_empty_is_not_dependency_failure():
    ex, _ = executor(Transport(feed(empty=True)))
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert result.status == "empty" and result.issue.code == "empty_result"
    assert result.payload["papers"] == []


@pytest.mark.parametrize(
    "status,category,issue",
    [
        (400, "invalid_input", "invalid_arguments"),
        (401, "unavailable", "internal_failure"),
        (403, "unavailable", "internal_failure"),
        (429, "unavailable", "dependency_failure"),
        (500, "unavailable", "dependency_failure"),
        (503, "unavailable", "dependency_failure"),
    ],
)
def test_http_failure_is_classified_with_bounded_retry(status, category, issue):
    transport = Transport(HTTPResponse(status, b"private upstream body", "text/plain"))
    ex, _ = executor(transport)
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert result.status == "error" and result.issue.code == issue
    assert result.payload["error_category"] == category
    expected = 2 if status in {429, 500, 503} else 1
    assert len(transport.calls) == expected
    assert result.payload["execution"]["attempts"] == expected
    assert "private upstream body" not in result.model_dump_json()


@pytest.mark.parametrize(
    "error,category",
    [
        (TimeoutError("private timeout"), "timeout"),
        (ConnectionError("private connection"), "unavailable"),
        (RuntimeError("private unexpected"), "error"),
    ],
)
def test_transport_error_feedback_is_safe(error, category, monkeypatch):
    secret = "private-test-secret-123456789"
    monkeypatch.setenv("B1_TEST_API_KEY", secret)
    ex, _ = executor(Transport(error=error))
    result = ex.execute("search_arxiv", {"query": QUERY})
    serialized = result.model_dump_json()
    assert result.status == "error" and result.payload["error_category"] == category
    assert all(
        x not in serialized
        for x in [
            secret,
            "private timeout",
            "private connection",
            "private unexpected",
            "Traceback",
            "/home/",
        ]
    )
    assert (
        "error_category" in ex.traces[0].model_dump_json()
        or ex.traces[0].status == "error"
    )


@pytest.mark.parametrize(
    "response",
    [
        HTTPResponse(200, b"not XML", "application/atom+xml"),
        HTTPResponse(200, b"<html/>", "text/html"),
        HTTPResponse(200, b"<html/>", "application/xml"),
        HTTPResponse(
            200, b'<!DOCTYPE feed [<!ENTITY x "bad">]><feed/>', "application/xml"
        ),
        HTTPResponse(200, b"x" * (MAX_RESPONSE_BYTES + 1), "application/xml"),
        feed(identity="../../etc/passwd"),
        feed(title=""),
        feed(author=""),
        feed(published="bad"),
    ],
)
def test_invalid_upstream_response_never_becomes_empty_or_valid_paper(response):
    ex, _ = executor(Transport(response))
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert result.status == "error" and result.issue.code == "internal_failure"
    assert result.result_count == 0 and "papers" not in result.payload


def test_http_200_error_atom_entry_is_not_a_paper():
    response = feed()
    response = HTTPResponse(
        200,
        response.body.replace(
            b"http://arxiv.org/abs/" + IDENTITY.encode(),
            b"http://arxiv.org/api/errors#bad_query",
        ),
        response.content_type,
    )
    ex, _ = executor(Transport(response))
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert (
        result.status == "error"
        and result.payload["error_code"] == "upstream_query_error"
    )
    assert result.issue.code == "invalid_arguments"


def test_abstract_is_source_text_or_explicitly_missing_and_truncated():
    result = parse_response(feed(summary="a" * 5000), top_k=1)
    assert (
        result.papers[0].abstract == "a" * 4000 and result.papers[0].abstract_truncated
    )
    result = parse_response(feed(summary=""), top_k=1)
    assert result.papers[0].abstract is None and not result.papers[0].abstract_truncated


def test_only_exact_identifier_and_version_link_to_local_paper():
    catalog = load_paper_catalog()
    identity = "2103.00020v1"
    ex, _ = executor(
        Transport(feed(identity=identity)),
        catalog=catalog,
        prepared_ids=["clip-2103.00020v1"],
    )
    first = ex.execute("search_arxiv", {"query": QUERY}).payload["papers"][0]
    assert (
        first["paper_id"] == "clip-2103.00020v1" and first["paper_evidence_available"]
    )
    ex, _ = executor(
        Transport(feed(identity="2103.00020v2")),
        catalog=catalog,
        prepared_ids=["clip-2103.00020v1"],
    )
    second = ex.execute("search_arxiv", {"query": QUERY}).payload["papers"][0]
    assert (
        second["paper_id"] == "arxiv:2103.00020v2"
        and not second["paper_evidence_available"]
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///home/private",
        "https://evil.invalid/abs/2103.00020v1",
        "https://arxiv.org@evil.invalid/abs/2103.00020v1",
        "https://arxiv.org/abs/2103.00020v1?x=1",
    ],
)
def test_invalid_source_url_rejected(url):
    with pytest.raises(ValueError):
        arxiv_identity(url)


def test_client_reuses_single_gate_and_respects_interval():
    transport = Transport()
    clock = Clock()
    api = ArxivClient(transport=transport, clock=clock, sleeper=clock.sleep)
    api.search(QUERY)
    api.search(QUERY)
    assert clock.delays == [3.0] and len(transport.calls) == 2


def test_rate_limit_wait_is_bounded_and_does_not_send_request():
    transport = Transport()
    clock = Clock()
    api = ArxivClient(
        timeout_seconds=1, transport=transport, clock=clock, sleeper=clock.sleep
    )
    api.search(QUERY)
    with pytest.raises(ArxivFailure) as exc:
        api.search(QUERY)
    assert exc.value.category == "timeout" and len(transport.calls) == 1


@pytest.mark.parametrize("value", ["nan", "0", "31", "bad", None])
def test_enabled_invalid_configuration_returns_observation_not_startup_failure(value):
    api = configured_arxiv_client(
        {"AGENT_ARXIV_ENABLED": "yes", "AGENT_ARXIV_TIMEOUT_SECONDS": value}
    )
    registry = ToolRegistry()
    registry.register(SearchArxivTool(api))
    result = ToolExecutor(registry).execute("search_arxiv", {"query": QUERY})
    assert (
        result.status == "error"
        and result.payload["error_code"] == "configuration_invalid"
    )

    assert not result.issue.retryable


def test_disabled_ignores_unrelated_bad_configuration():
    assert configured_arxiv_client({"AGENT_ARXIV_TIMEOUT_SECONDS": "bad"}) is None
    assert (
        configured_arxiv_client(
            {"AGENT_ARXIV_ENABLED": "false", "AGENT_ARXIV_TIMEOUT_SECONDS": "bad"}
        )
        is None
    )
    assert configured_arxiv_client({"AGENT_ARXIV_ENABLED": "unexpected"}) is not None


def test_fake_fixture_provenance_hash_is_not_invented():
    response = feed()
    result = parse_response(response, top_k=1)
    assert result.response_sha256 == hashlib.sha256(response.body).hexdigest()
    with pytest.raises(ValidationError):
        SearchArxivInput(query=QUERY, top_k=False)
    assert json.loads(SearchArxivInput(query=QUERY).model_dump_json())["top_k"] == 5


def test_duplicate_source_ids_are_deduplicated_without_fabricated_score():
    response = feed()
    entry = response.body.split(b"<entry>")[1].split(b"</entry>")[0]
    repeated = HTTPResponse(
        200,
        response.body.replace(b"</feed>", b"<entry>" + entry + b"</entry></feed>"),
        response.content_type,
    )
    result = parse_response(repeated, top_k=2)
    assert len(result.papers) == 1


def test_bad_record_after_valid_record_rejects_entire_response():
    response = feed()
    broken = b"<entry><id>https://evil.invalid/not-a-paper</id></entry>"
    mixed = HTTPResponse(
        200,
        response.body.replace(b"</feed>", broken + b"</feed>"),
        response.content_type,
    )
    ex, _ = executor(Transport(mixed))
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert result.status == "error" and result.result_count == 0


def test_encoded_doctype_cannot_hide_from_guard():
    document = '<!DOCTYPE feed [<!ENTITY x "private">]><feed xmlns="http://www.w3.org/2005/Atom"/>'
    response = HTTPResponse(200, document.encode("utf-16"), "application/atom+xml")
    with pytest.raises(ArxivFailure):
        parse_response(response, top_k=1)


@pytest.mark.parametrize(
    "identity", ["hep-th/9901001v1", "math.AG/0301001v2", "2103.00020v1"]
)
def test_source_identity_supports_official_old_and_new_styles(identity):
    assert arxiv_identity("https://arxiv.org/abs/" + identity) == identity


def test_http_resource_closes_on_success_and_body_read_failure(monkeypatch):
    from app.papers import arxiv

    states = []

    class Response:
        status = 200

        def __init__(self):
            self.headers = {"Content-Type": "application/atom+xml"}
            self.broken = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            states.append("closed")

        def read(self, maximum):
            assert maximum == MAX_RESPONSE_BYTES + 1
            if self.broken:
                raise ConnectionError("private read error")
            return feed().body

    response = Response()

    class Opener:
        def open(self, request, timeout):
            assert "authorization" not in {k.lower() for k in request.headers}
            assert timeout == 2
            return response

    monkeypatch.setattr(arxiv.urllib.request, "build_opener", lambda *args: Opener())
    assert arxiv._http_request(arxiv.ENDPOINT, 2).status == 200
    response.broken = True
    with pytest.raises(ConnectionError):
        arxiv._http_request(arxiv.ENDPOINT, 2)
    assert states == ["closed", "closed"]


def test_bare_feed_is_invalid_response_not_proven_empty_result():
    ex, _ = executor(
        Transport(
            HTTPResponse(
                200,
                b'<feed xmlns="http://www.w3.org/2005/Atom"/>',
                "application/atom+xml",
            )
        )
    )
    result = ex.execute("search_arxiv", {"query": QUERY})
    assert (
        result.status == "error"
        and result.payload["error_code"] == "invalid_upstream_response"
    )
