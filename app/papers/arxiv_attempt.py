"""一个HTTP尝试的独立进程入口；读取、解析与输出都在可终止边界内。"""

import json
import sys
import urllib.parse

from app.papers.arxiv import (
    ArxivFailure,
    _http_request,
    network_failure,
    parse_response,
)
from app.papers.arxiv_transport import MAX_RESULT_BYTES, validated_endpoint


def main():
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("input_too_large")
        request = json.loads(raw)
        endpoint = validated_endpoint(request["endpoint"])
        parameters = urllib.parse.urlencode(
            {
                "search_query": request["query"],
                "start": 0,
                "max_results": request["top_k"],
            }
        )
        try:
            response = _http_request(endpoint + "?" + parameters, request["timeout"])
            result = parse_response(response, top_k=request["top_k"])
            output = {"kind": "success", "result": result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001 - 子进程只交付安全分类，不输出堆栈
            failure = exc if isinstance(exc, ArxivFailure) else network_failure(exc)
            output = {
                "kind": "error",
                "category": failure.category,
                "code": failure.code,
                "retry_after": failure.retry_after,
            }
    except Exception:  # noqa: BLE001 - 不在stdout/stderr泄露输入或环境
        output = {
            "kind": "error",
            "category": "error",
            "code": "arxiv_worker_protocol_failure",
        }
    data = json.dumps(output, ensure_ascii=False).encode()
    if len(data) > MAX_RESULT_BYTES:
        data = b'{"kind":"error","category":"error","code":"response_too_large"}'
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
