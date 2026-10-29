"""有限字面匹配；SQL与离线夹具共用权重和稳定排序。"""

KEYWORD_WEIGHT = 2
KEY_WEIGHT = 3
TAG_WEIGHT = 2


def literal_pattern(value):
    return (
        "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )


def match_score(record, request):
    return sum(
        KEYWORD_WEIGHT * (term in record.content.lower())
        + KEY_WEIGHT * (term in record.memory_key.lower())
        for term in request.keywords
    ) + sum(TAG_WEIGHT * (tag in record.tags) for tag in request.tags)


def matches(record, request, now):
    if not record.active_at(now):
        return False
    if request.memory_types and record.memory_type not in request.memory_types:
        return False
    if request.keywords and not any(
        term in record.content.lower() or term in record.memory_key.lower()
        for term in request.keywords
    ):
        return False
    return not request.tags or bool(set(request.tags).intersection(record.tags))


def rank_records(records, request, now):
    values = [r for r in records if matches(r, request, now)]
    values.sort(key=lambda r: r.memory_id)
    values.sort(key=lambda r: r.updated_at, reverse=True)
    values.sort(key=lambda r: match_score(r, request), reverse=True)
    return values[: request.top_k]
