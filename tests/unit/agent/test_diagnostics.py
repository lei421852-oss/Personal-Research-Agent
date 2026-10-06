from types import SimpleNamespace

import httpx
from openai import BadRequestError

from app.diagnostics import failure_details
from app.llm.provider import OpenAICompatibleLLMClient


def test_http_failure_has_safe_cause_and_status(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-confidential-value")
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    )
    error = BadRequestError(
        "request failed",
        response=response,
        body={
            "message": "Thinking mode does not support this tool_choice sk-test-confidential-value /home/secret/file",
            "code": "invalid_request_error",
        },
    )
    details = failure_details(error, phase="llm_complete", summary="Request failed")
    assert details["http_status"] == 400
    assert "Thinking mode" in details["message"]
    assert "sk-test-confidential-value" not in str(details)
    assert "/home/secret" not in str(details)
    assert "Traceback" not in str(details)


def test_generic_failure_omits_arbitrary_exception_text():
    details = failure_details(
        RuntimeError("api_key=private-value /home/secret/file"),
        phase="execution",
        summary="Internal failure",
    )
    assert details["error_type"] == "RuntimeError"
    assert details["message"] == "Internal failure"
    assert "private-value" not in str(details)


def test_adapter_records_http_diagnostic_without_raw_response():
    response = httpx.Response(401, request=httpx.Request("POST", "https://unused"))

    def fail(**kwargs):
        raise BadRequestError(
            "fail", response=response, body={"message": "Authentication failed"}
        )

    adapter = OpenAICompatibleLLMClient(
        api_key="fake",
        model="fake",
        base_url="https://unused",
        sdk_client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fail))
        ),
    )
    try:
        adapter.complete(messages=[], tools=[])
    except BadRequestError:
        pass
    assert adapter.calls[0]["failure"]["http_status"] == 401
    assert adapter.calls[0]["failure"]["message"] == "Authentication failed"
    assert "headers" not in str(adapter.calls)
