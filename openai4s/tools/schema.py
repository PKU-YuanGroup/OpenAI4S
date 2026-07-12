"""Small, dependency-free JSON Schema contracts for control-tool inputs.

The provider schema is a model hint, not a security boundary.  This module
implements the deliberately small subset OpenAI4S advertises and enforces the
same contract again immediately before a model-originated tool reaches the
HostDispatcher.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

SUPPORTED_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean"}
)
UNKNOWN_PROPERTY_POLICIES = frozenset({"allow", "forbid"})

_ASSERTION_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)
_ANNOTATION_KEYWORDS = frozenset({"description", "title", "default"})
_ALLOWED_KEYWORDS = _ASSERTION_KEYWORDS | _ANNOTATION_KEYWORDS


class SchemaDefinitionError(ValueError):
    """Raised when a trusted tool declares a schema outside our subset."""


@dataclass(frozen=True)
class ValidationIssue:
    """One stable, user/model-readable input-validation failure."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def normalize_object_schema(
    schema: dict | None, *, unknown_properties: str = "forbid"
) -> dict:
    """Return an isolated root-object schema with an explicit unknown policy."""
    if unknown_properties not in UNKNOWN_PROPERTY_POLICIES:
        raise SchemaDefinitionError("unknown_properties must be 'allow' or 'forbid'")
    if schema is None:
        normalized: dict = {}
    elif isinstance(schema, dict):
        normalized = copy.deepcopy(schema)
    else:
        raise SchemaDefinitionError("tool parameters must be a JSON object schema")
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    normalized.setdefault("additionalProperties", unknown_properties == "allow")
    validate_schema_definition(normalized)
    if normalized.get("type") != "object":
        raise SchemaDefinitionError("tool parameters must have type 'object'")
    return normalized


def validate_schema_definition(schema: Any, *, path: str = "$") -> None:
    """Validate a trusted schema declaration against the supported subset."""
    _validate_schema_definition(schema, path=path, depth=0)


def _validate_schema_definition(schema: Any, *, path: str, depth: int) -> None:
    if depth > 64:
        raise SchemaDefinitionError(f"{path}: schema nesting exceeds 64 levels")
    if not isinstance(schema, dict):
        raise SchemaDefinitionError(f"{path}: schema must be an object")
    unsupported = sorted(set(schema) - _ALLOWED_KEYWORDS)
    if unsupported:
        raise SchemaDefinitionError(
            f"{path}: unsupported schema keyword(s): {', '.join(unsupported)}"
        )

    declared_type = schema.get("type")
    if declared_type is not None and declared_type not in SUPPORTED_TYPES:
        raise SchemaDefinitionError(
            f"{path}.type: expected one of {sorted(SUPPORTED_TYPES)!r}"
        )

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            raise SchemaDefinitionError(f"{path}.enum: expected a non-empty array")
        for value in enum:
            if not _is_json_value(value):
                raise SchemaDefinitionError(
                    f"{path}.enum: values must be JSON-compatible"
                )

    for keyword in ("minimum", "maximum"):
        if keyword in schema and not _is_number(schema[keyword]):
            raise SchemaDefinitionError(f"{path}.{keyword}: expected a number")
    if (
        "minimum" in schema
        and "maximum" in schema
        and schema["minimum"] > schema["maximum"]
    ):
        raise SchemaDefinitionError(f"{path}: minimum cannot exceed maximum")

    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        if keyword in schema:
            value = schema[keyword]
            if not _is_integer(value) or value < 0:
                raise SchemaDefinitionError(
                    f"{path}.{keyword}: expected a non-negative integer"
                )
    for lower, upper in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if lower in schema and upper in schema and schema[lower] > schema[upper]:
            raise SchemaDefinitionError(f"{path}: {lower} cannot exceed {upper}")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict) or not all(
            isinstance(key, str) for key in properties
        ):
            raise SchemaDefinitionError(
                f"{path}.properties: expected an object with string keys"
            )
        if declared_type not in (None, "object"):
            raise SchemaDefinitionError(
                f"{path}.properties: only valid for an object schema"
            )
        for key, child in properties.items():
            _validate_schema_definition(
                child, path=_child_path(path, key), depth=depth + 1
            )

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise SchemaDefinitionError(
                f"{path}.required: expected an array of property names"
            )
        if len(set(required)) != len(required):
            raise SchemaDefinitionError(f"{path}.required: contains duplicates")
        if properties is None and required:
            raise SchemaDefinitionError(
                f"{path}.required: properties must also be declared"
            )
        unknown_required = sorted(set(required) - set(properties or {}))
        if unknown_required:
            raise SchemaDefinitionError(
                f"{path}.required: undeclared property name(s): "
                + ", ".join(unknown_required)
            )

    additional = schema.get("additionalProperties")
    if additional is not None:
        if declared_type not in (None, "object"):
            raise SchemaDefinitionError(
                f"{path}.additionalProperties: only valid for an object schema"
            )
        if not isinstance(additional, (bool, dict)):
            raise SchemaDefinitionError(
                f"{path}.additionalProperties: expected boolean or schema"
            )
        if isinstance(additional, dict):
            _validate_schema_definition(
                additional,
                path=f"{path}.additionalProperties",
                depth=depth + 1,
            )

    if "items" in schema:
        if declared_type not in (None, "array"):
            raise SchemaDefinitionError(f"{path}.items: only valid for an array schema")
        _validate_schema_definition(schema["items"], path=f"{path}[]", depth=depth + 1)


def validate_json_schema(
    value: Any,
    schema: dict,
    *,
    unknown_properties: str = "forbid",
    max_issues: int = 16,
) -> tuple[ValidationIssue, ...]:
    """Validate ``value`` and return bounded issues; malformed inputs never raise.

    ``unknown_properties`` is used when an object schema omits
    ``additionalProperties``.  A schema may explicitly override it with true,
    false, or a schema applied to every additional value.
    """
    if unknown_properties not in UNKNOWN_PROPERTY_POLICIES:
        raise ValueError("unknown_properties must be 'allow' or 'forbid'")
    if max_issues < 1:
        raise ValueError("max_issues must be at least 1")
    validate_schema_definition(schema)
    issues: list[ValidationIssue] = []
    _validate_value(
        value,
        schema,
        path="$",
        unknown_properties=unknown_properties,
        issues=issues,
        max_issues=max_issues,
        depth=0,
    )
    return tuple(issues)


def _validate_value(
    value: Any,
    schema: dict,
    *,
    path: str,
    unknown_properties: str,
    issues: list[ValidationIssue],
    max_issues: int,
    depth: int,
) -> None:
    if len(issues) >= max_issues:
        return
    if depth > 64:
        issues.append(ValidationIssue(path, "value nesting exceeds 64 levels"))
        return

    declared_type = schema.get("type")
    if declared_type is not None and not _matches_type(value, declared_type):
        issues.append(
            ValidationIssue(
                path,
                f"expected {declared_type}, got {_json_type_name(value)}",
            )
        )
        return

    if "enum" in schema and not any(
        _json_equal(value, candidate) for candidate in schema["enum"]
    ):
        issues.append(ValidationIssue(path, f"must be one of {schema['enum']!r}"))
        if len(issues) >= max_issues:
            return

    if _is_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            issues.append(ValidationIssue(path, f"must be >= {minimum}"))
        if len(issues) >= max_issues:
            return
        if maximum is not None and value > maximum:
            issues.append(ValidationIssue(path, f"must be <= {maximum}"))
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            issues.append(ValidationIssue(path, f"length must be >= {minimum}"))
        if len(issues) >= max_issues:
            return
        if maximum is not None and len(value) > maximum:
            issues.append(ValidationIssue(path, f"length must be <= {maximum}"))
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            issues.append(ValidationIssue(path, f"item count must be >= {minimum}"))
        if len(issues) >= max_issues:
            return
        if maximum is not None and len(value) > maximum:
            issues.append(ValidationIssue(path, f"item count must be <= {maximum}"))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(
                    item,
                    item_schema,
                    path=f"{path}[{index}]",
                    unknown_properties=unknown_properties,
                    issues=issues,
                    max_issues=max_issues,
                    depth=depth + 1,
                )
                if len(issues) >= max_issues:
                    return
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for required in schema.get("required") or []:
            if required not in value:
                issues.append(
                    ValidationIssue(
                        _child_path(path, required), "required property is missing"
                    )
                )
                if len(issues) >= max_issues:
                    return
        additional = schema.get("additionalProperties")
        if additional is None:
            additional = unknown_properties == "allow"
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                _validate_value(
                    item,
                    child_schema,
                    path=_child_path(path, key),
                    unknown_properties=unknown_properties,
                    issues=issues,
                    max_issues=max_issues,
                    depth=depth + 1,
                )
            elif additional is False:
                issues.append(
                    ValidationIssue(
                        _child_path(path, key), "unknown property is not allowed"
                    )
                )
            elif isinstance(additional, dict):
                _validate_value(
                    item,
                    additional,
                    path=_child_path(path, key),
                    unknown_properties=unknown_properties,
                    issues=issues,
                    max_issues=max_issues,
                    depth=depth + 1,
                )
            if len(issues) >= max_issues:
                return


def provider_strict_compatible(schema: dict) -> bool:
    """Whether the schema fits the portable strict-function subset.

    OpenAI-compatible strict function calling requires closed objects and all
    declared properties to appear in ``required``.  We apply that recursively;
    provider adapters that do not expose a strict switch simply ignore it.
    """
    try:
        validate_schema_definition(schema)
    except SchemaDefinitionError:
        return False
    return _strict_node(schema)


def _strict_node(schema: dict) -> bool:
    declared_type = schema.get("type")
    if declared_type == "object":
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is not False:
            return False
        if set(schema.get("required") or []) != set(properties):
            return False
        if not all(_strict_node(child) for child in properties.values()):
            return False
    elif declared_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict) or not _strict_node(items):
            return False
    return True


def _matches_type(value: Any, declared_type: str) -> bool:
    if declared_type == "object":
        return isinstance(value, dict)
    if declared_type == "array":
        return isinstance(value, list)
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "integer":
        return _is_integer(value)
    if declared_type == "number":
        return _is_number(value)
    return False


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _json_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if _is_integer(value):
        return "integer"
    if _is_number(value):
        return "number"
    return type(value).__name__


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_number(left) and _is_number(right):
        return left == right
    return type(left) is type(right) and left == right


def _is_json_value(value: Any, *, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or isinstance(value, (str, bool)) or _is_number(value):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _child_path(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"{path}['{escaped}']"


__all__ = [
    "SUPPORTED_TYPES",
    "UNKNOWN_PROPERTY_POLICIES",
    "SchemaDefinitionError",
    "ValidationIssue",
    "normalize_object_schema",
    "provider_strict_compatible",
    "validate_json_schema",
    "validate_schema_definition",
]
