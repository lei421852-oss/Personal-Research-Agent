"""来源与敏感信息规则；不使用模型判断或自动提取。"""

import hashlib
import re

from app.memory.errors import MemoryFailure
from app.memory.models import MemorySource

SENSITIVE = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:api[_ -]?key|password|密码|口令|secret|token)\s*[:=：]\s*\S+"
    r"|Bearer\s+[A-Za-z0-9._-]{8,}"
    r"|(?:postgres(?:ql)?(?:\+asyncpg)?|mysql|redis)://[^\s]+"
    r"|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    r"|\b1[3-9]\d{9}\b|\b\d{17}[\dXx]\b",
    re.IGNORECASE,
)


def validate_safe(*values):
    if any(SENSITIVE.search(value) for value in values):
        raise MemoryFailure("memory_sensitive_content")


def bind_source(question, content):
    # 来源原文片段校验，不允许模型摘要或推测冒充已确认来源。
    if not content or content not in question:
        raise MemoryFailure("memory_source_rejected")
    validate_safe(content)
    return MemorySource(
        source_ref="question_sha256:"
        + hashlib.sha256(question.encode("utf-8")).hexdigest(),
        source_excerpt=content,
    )
