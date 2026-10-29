"""只映射明确支持的JSON Schema子集；未知特性一律拒绝，避免静默放宽。"""

import math
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    create_model,
)

from app.tools.integrations.error_mapper import ExternalToolError

MAX_SCHEMA_NODES = 128
MAX_SCHEMA_DEPTH = 4

_COMMON = {"type", "title", "description"}
_KEYS = {
    "object": {"properties", "required", "additionalProperties"},
    "string": {"enum", "minLength", "maxLength"},
    "integer": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"},
    "number": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"},
    "boolean": set(),
    "array": {"items", "minItems", "maxItems"},
}


def compile_input_schema(schema):
    counter = [0]
    visited = [0]

    def fail():
        raise ExternalToolError("unsupported_schema")

    def node(spec, depth):
        visited[0] += 1
        if (
            type(spec) is not dict
            or depth > MAX_SCHEMA_DEPTH
            or visited[0] > MAX_SCHEMA_NODES
        ):
            fail()
        kind = spec.get("type")
        if not isinstance(kind, str) or kind not in _KEYS:
            fail()
        if set(spec) - _COMMON - _KEYS[kind]:
            fail()
        for key in ("title", "description"):
            if key in spec and (not isinstance(spec[key], str) or len(spec[key]) > 500):
                fail()
        limits = {}
        if kind == "object":
            properties = spec.get("properties", {})
            required = spec.get("required", [])
            if (
                type(properties) is not dict
                or len(properties) > 32
                or type(required) is not list
                or any(type(x) is not str for x in required)
                or len(required) != len(set(required))
                or not set(required).issubset(properties)
                or spec.get("additionalProperties") is not False
            ):
                fail()
            fields = {}
            for key, child in properties.items():
                if (
                    not isinstance(key, str)
                    or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,49}", key)
                    or hasattr(BaseModel, key)
                ):
                    fail()
                annotation = node(child, depth + 1)
                # 未给出的可选字段不发送给服务；显式null仍须通过原类型校验。
                fields[key] = (
                    annotation,
                    Field(default=... if key in required else None),
                )
            counter[0] += 1
            return create_model(
                f"ExternalArguments{counter[0]}",
                __config__=ConfigDict(extra="forbid", strict=True, frozen=True),
                **fields,
            )
        if kind in {"string", "array"}:
            for key, field in (
                ("minLength" if kind == "string" else "minItems", "min_length"),
                ("maxLength" if kind == "string" else "maxItems", "max_length"),
            ):
                if key in spec:
                    if type(spec[key]) is not int or spec[key] < 0:
                        fail()
                    limits[field] = spec[key]
            if limits.get("min_length", 0) > limits.get("max_length", math.inf):
                fail()
        if kind == "string":
            annotation = StrictStr
            if "enum" in spec:
                values = spec["enum"]
                if (
                    type(values) is not list
                    or not values
                    or len(values) > 32
                    or any(type(x) is not str for x in values)
                    or len(values) != len(set(values))
                ):
                    fail()
                annotation = Literal[tuple(values)]
        elif kind == "array":
            if "items" not in spec:
                fail()
            annotation = list[node(spec["items"], depth + 1)]
        elif kind == "boolean":
            annotation = StrictBool
        else:
            annotation = (
                StrictInt
                if kind == "integer"
                else Annotated[float, Field(strict=True, allow_inf_nan=False)]
            )
            for key, field in (
                ("minimum", "ge"),
                ("maximum", "le"),
                ("exclusiveMinimum", "gt"),
                ("exclusiveMaximum", "lt"),
            ):
                if key in spec:
                    bound = spec[key]
                    if type(bound) not in {int, float} or not math.isfinite(bound):
                        fail()
                    limits[field] = bound
            if max(limits.get("ge", -math.inf), limits.get("gt", -math.inf)) > min(
                limits.get("le", math.inf), limits.get("lt", math.inf)
            ):
                fail()
        return Annotated[annotation, Field(**limits)] if limits else annotation

    if type(schema) is not dict or schema.get("type") != "object":
        raise ExternalToolError("unsupported_schema")
    return node(schema, 0)
