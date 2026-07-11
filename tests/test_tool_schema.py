"""Pure-stdlib Host validation for the supported control-tool schema subset."""

import pytest

from openai4s.tools.schema import (
    SchemaDefinitionError,
    normalize_object_schema,
    provider_strict_compatible,
    validate_json_schema,
)

_NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "enum": ["alpha", "beta"],
            "minLength": 4,
            "maxLength": 5,
        },
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "count": {"type": "integer", "minimum": 1, "maximum": 3},
        "enabled": {"type": "boolean"},
        "samples": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string", "minLength": 1}},
                "required": ["label"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "score", "count", "enabled", "samples"],
    "additionalProperties": False,
}


def test_supported_nested_schema_accepts_valid_json_value():
    value = {
        "name": "alpha",
        "score": 0.5,
        "count": 2,
        "enabled": True,
        "samples": [{"label": "x"}],
    }

    assert validate_json_schema(value, _NESTED_SCHEMA) == ()


def test_constraints_and_nested_paths_are_reported_together():
    value = {
        "name": "gamma",
        "score": 2,
        "count": True,
        "enabled": "yes",
        "samples": [{"label": "", "extra": 1}, {}],
        "unexpected": 1,
    }

    messages = [str(issue) for issue in validate_json_schema(value, _NESTED_SCHEMA)]

    assert "$.name: must be one of ['alpha', 'beta']" in messages
    assert "$.score: must be <= 1" in messages
    assert "$.count: expected integer, got boolean" in messages
    assert "$.enabled: expected boolean, got string" in messages
    assert "$.samples[0].label: length must be >= 1" in messages
    assert "$.samples[0].extra: unknown property is not allowed" in messages
    assert "$.samples[1].label: required property is missing" in messages
    assert "$.unexpected: unknown property is not allowed" in messages


def test_required_array_bounds_and_string_max_length():
    issues = validate_json_schema(
        {"name": "alphabet", "score": -1, "count": 4, "enabled": False},
        _NESTED_SCHEMA,
    )
    messages = [str(issue) for issue in issues]

    assert "$.name: length must be <= 5" in messages
    assert "$.score: must be >= 0" in messages
    assert "$.count: must be <= 3" in messages
    assert "$.samples: required property is missing" in messages


def test_unknown_property_policy_and_schema_for_additional_values():
    open_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    typed_additional = {
        **open_schema,
        "additionalProperties": {"type": "integer"},
    }

    assert validate_json_schema(
        {"extension": 1}, open_schema, unknown_properties="allow"
    ) == ()
    assert "unknown property" in str(
        validate_json_schema(
            {"extension": 1}, open_schema, unknown_properties="forbid"
        )[0]
    )
    assert validate_json_schema({"extension": 1}, typed_additional) == ()
    assert "expected integer" in str(
        validate_json_schema({"extension": "one"}, typed_additional)[0]
    )


def test_normalization_closes_tool_root_without_mutating_source():
    source = {"properties": {"value": {"type": "string"}}, "required": []}

    normalized = normalize_object_schema(source)
    normalized["properties"]["value"]["description"] = "changed"

    assert normalized["type"] == "object"
    assert normalized["additionalProperties"] is False
    assert "description" not in source["properties"]["value"]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "null"},
        {"type": "string", "unknownKeyword": True},
        {"type": "array", "items": "not-a-schema"},
        {"type": "object", "properties": {}, "required": ["missing"]},
        {"type": "string", "minLength": 3, "maxLength": 2},
    ],
)
def test_malformed_or_unsupported_trusted_schema_is_rejected(schema):
    with pytest.raises(SchemaDefinitionError):
        validate_json_schema(None, schema)


def test_provider_strict_requires_recursively_closed_required_objects():
    strict = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    optional = {**strict, "required": []}
    open_object = {**strict, "additionalProperties": True}

    assert provider_strict_compatible(strict) is True
    assert provider_strict_compatible(optional) is False
    assert provider_strict_compatible(open_object) is False
