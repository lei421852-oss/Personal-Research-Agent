"""对公开观察结果和 Trace 脱敏，不修改领域证据。"""

import math
import os
import re
from collections.abc import Mapping
from typing import Any

from pydantic import JsonValue

_UNIX_PATH = re.compile(r"(?<![:/\w])/(?:[\w.~-]+/)+[\w./~-]*")
_WINDOWS_PATH = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s\"<>]+|\\\\[^\s\"<>]+")
_FILE_URI = re.compile(r'file://[^\s"<>]+', re.IGNORECASE)
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
_TRACEBACK = re.compile(r"Traceback \(most recent call last\):.*", re.DOTALL)
_SENSITIVE_NAMES = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential)", re.IGNORECASE
)


def redact_text(value: str) -> str:
    # 只读取进程环境，不查找其他项目的 .env 配置。
    secrets = [
        v for k, v in os.environ.items() if _SENSITIVE_NAMES.search(k) and len(v) >= 8
    ]
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, "[redacted]")
    value = _API_KEY.sub("[redacted]", value)
    value = _TRACEBACK.sub("[redacted traceback]", value)
    value = _FILE_URI.sub("[redacted path]", value)
    value = _WINDOWS_PATH.sub("[redacted path]", value)
    return _UNIX_PATH.sub("[redacted path]", value)


def public_value(value: Any) -> JsonValue:
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[unsupported number]"
    if isinstance(value, Mapping):
        return {
            redact_text(str(k)): "[redacted]"
            if _SENSITIVE_NAMES.search(str(k))
            else public_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [public_value(v) for v in value]
    return "[unsupported value]"  # 不对任意对象或异常调用 repr，避免间接泄露内部信息。
