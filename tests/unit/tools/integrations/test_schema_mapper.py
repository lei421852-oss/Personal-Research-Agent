"""Schema子集的严格性与拒绝策略。"""

import pytest
from pydantic import ValidationError

from app.tools.integrations.error_mapper import ExternalToolError
from app.tools.integrations.schema_mapper import compile_input_schema


def schema(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {"query": {"type": "string", "minLength": 1}},
        "required": ["query"] if required is None else required,
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    "arguments",
    [{}, {"query": 3}, {"query": ""}, {"query": None}, {"query": "ok", "extra": 1}],
)
def test_required_types_and_extra_fields_are_strict(arguments):
    model = compile_input_schema(schema())
    with pytest.raises(ValidationError):
        model.model_validate(arguments)


def test_optional_omission_does_not_send_null():
    model = compile_input_schema(schema({"optional": {"type": "integer"}}, []))
    assert model.model_validate({}).model_dump(exclude_unset=True) == {}
    with pytest.raises(ValidationError):
        model.model_validate({"optional": None})


def test_nested_array_enum_and_number_constraints():
    model = compile_input_schema(
        schema(
            {
                "query": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["a", "b"]},
                    "minItems": 1,
                    "maxItems": 2,
                },
                "nested": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "minimum": 1, "maximum": 2}
                    },
                    "required": ["count"],
                    "additionalProperties": False,
                },
                "ratio": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                "flag": {"type": "boolean"},
            },
            ["query", "nested", "ratio", "flag"],
        )
    )
    values = {"query": ["a"], "nested": {"count": 2}, "ratio": 0.5, "flag": True}
    assert model.model_validate(values).model_dump() == values
    for key, value in [
        ("query", ["x"]),
        ("query", []),
        ("nested", {"count": True}),
        ("nested", {"count": 1, "extra": 1}),
        ("ratio", float("nan")),
        ("ratio", "0.5"),
        ("ratio", 0),
        ("flag", 1),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate(values | {key: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"additionalProperties": True},
        {"$ref": "#/anything"},
        {"anyOf": []},
        {"default": {}},
        {"format": "uri"},
        {"required": ["missing"]},
        {"required": ["query", "query"]},
        {"patternProperties": {}},
        {"type": ["object", "null"]},
    ],
)
def test_unsupported_or_invalid_root_schema_is_rejected(changes):
    with pytest.raises(ExternalToolError, match="unsupported_schema"):
        compile_input_schema(schema() | changes)


@pytest.mark.parametrize(
    "child",
    [
        {"type": "string", "pattern": ".*"},
        {"type": "string", "enum": ["x", "x"]},
        {"type": "string", "enum": [1]},
        {"type": "string", "minLength": True},
        {"type": "integer", "minimum": False},
        {"type": "integer", "minimum": 2, "maximum": 1},
        {"type": "array"},
        {"type": "object", "additionalProperties": {}},
        {"type": "null"},
    ],
)
def test_unsupported_child_schema_is_rejected(child):
    with pytest.raises(ExternalToolError, match="unsupported_schema"):
        compile_input_schema(schema({"query": child}))


def test_reserved_field_cannot_change_model_configuration():
    with pytest.raises(ExternalToolError, match="unsupported_schema"):
        compile_input_schema(schema({"model_config": {"type": "string"}}, []))


def test_schema_expansion_is_bounded_before_building_unlimited_models():
    child = {
        "type": "object",
        "properties": {f"key{i}": {"type": "string"} for i in range(32)},
        "additionalProperties": False,
    }
    with pytest.raises(ExternalToolError, match="unsupported_schema"):
        compile_input_schema(schema({f"group{i}": child for i in range(10)}, []))
