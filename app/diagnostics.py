"""供人工排查的安全失败信息，不包含异常堆栈或原始请求体。"""

from pydantic import ValidationError

from app.tools.redaction import public_value, redact_text


def failure_details(exc: Exception, *, phase: str, summary: str) -> dict:
    details = {"phase": phase, "error_type": type(exc).__name__, "message": summary}
    safe_messages = {
        "Retrieval violated the requested Top-K limit",
        "Retrieval violated paper scope",
        "Figure lookup violated the requested identity",
        "Page lookup violated the requested identity",
        "Retrieval returned evidence outside this corpus snapshot",
        "Evidence is outside the current corpus",
        "Expected figure evidence",
        "Context must be text evidence from the figure's paper",
        "Citation list is empty",
        "Vision response cited unknown evidence",
        "Empty or invalid citation list",
        "Final citations include unobserved evidence",
        "An uncited finish must state evidence limitations",
        "Provider returned no choices",
        "Provider returned an incomplete action",
        "Unsupported provider action type",
        "Tool arguments must be a JSON object",
    }
    if not isinstance(exc, ValidationError) and str(exc) in safe_messages:
        details["message"] = str(exc)
    if isinstance(exc, ValueError) and type(exc).__name__ == "JSONDecodeError":
        details["message"] = "Provider tool arguments are not valid JSON."
        details["hint"] = (
            "Inspect public tool-call format; raw model output is not retained."
        )
    cause = exc
    for _ in range(3):
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    if cause is not exc:
        details["cause_type"] = type(cause).__name__
    status = getattr(cause, "status_code", None)
    if isinstance(status, int):
        details["http_status"] = status
        hints = {
            400: "Check provider-supported parameters and model mode.",
            401: "Check API key and provider endpoint.",
            403: "Check account/model access permissions.",
            429: "Check quota or rate limits.",
        }
        details["hint"] = hints.get(
            status, "Check provider availability and HTTP status."
        )
        # 兼容 OpenAI 的 SDK 单独提供结构化错误；不读取其中的请求头或原始请求。
        if type(cause).__module__.startswith("openai"):
            body = getattr(cause, "body", None)
            if isinstance(body, dict):
                error = body.get("error", body)
                if isinstance(error, dict):
                    message = error.get("message")
                    if isinstance(message, str):
                        details["message"] = redact_text(message)[:1200]
                    code = error.get("code")
                    if isinstance(code, (str, int)):
                        details["provider_code"] = code
    if isinstance(exc, ValidationError):
        details["validation_errors"] = [
            {"field": list(e["loc"]), "type": e["type"]}
            for e in exc.errors(
                include_input=False, include_context=False, include_url=False
            )
        ]
        details["hint"] = (
            "Check the listed fields against the tool schema; input values are omitted."
        )
    if "Timeout" in type(cause).__name__:
        details["hint"] = "Check request timeout and provider/network latency."
    elif "Connection" in type(cause).__name__:
        details["hint"] = "Check base URL, network, DNS and proxy settings."
    return public_value(details)
