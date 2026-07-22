"""Response shapes, derived from real responses rather than from route code.

The Contract scorecard wants the external surface covered by schema. The
obvious route -- generate it from the routing chain -- has an expensive
prerequisite: ``Handler._api`` is one method of ~2000 lines and ~150 branches,
with response bodies assembled inline, so nothing can be read off it until it
is decomposed. That decomposition is worth doing and is not a prerequisite for
having schemas.

So the dependency is inverted. Every JSON response leaves through a single
method, so what a route *actually returns* can be observed while the suite
exercises it, generalised into a shape, and frozen. Two consequences worth
stating plainly:

* a schema derived from real responses cannot drift from reality the way a
  hand-maintained one does -- if it disagrees, one of them is a bug and the
  test says which;
* coverage is partial at first, and *measurable*. A route no test exercises
  has no schema, which is a fact worth surfacing rather than a gap to hide.

Deliberately not JSON Schema draft-2020-12, and deliberately no dependency.
The core is stdlib-only, so this is a small, explicit subset: types, required
keys, and element shapes. It answers "did this response change shape", which
is the question the contract needs. It does not answer "is this valid against
an arbitrary published schema", and it does not pretend to.
"""
from __future__ import annotations

from typing import Any

#: What a JSON value's shape is called here. `null` is tracked separately from
#: a missing key: a field that is present-and-null is a different contract
#: from one that may be absent, and clients break on the difference.
_TYPES = ("null", "boolean", "integer", "number", "string", "array", "object")


def type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def infer(value: Any) -> dict[str, Any]:
    """The shape of one observed value."""
    kind = type_of(value)
    if kind == "object":
        return {
            "type": "object",
            "properties": {
                str(key): infer(item) for key, item in sorted(value.items())
            },
            # Every key of the first observation is required until a later
            # observation proves otherwise; `merge` demotes them. Starting
            # permissive would mean the schema never learns what is guaranteed.
            "required": sorted(str(key) for key in value),
        }
    if kind == "array":
        items: dict[str, Any] | None = None
        for item in value:
            observed = infer(item)
            items = observed if items is None else merge(items, observed)
        return {"type": "array", "items": items} if items else {"type": "array"}
    return {"type": kind}


def merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Generalise two shapes into one that admits both.

    Widening rather than replacing is the whole point: a route observed twice
    with different optional fields must end up with a schema that accepts each,
    not one that accepts only the most recent.
    """
    types = _type_set(left) | _type_set(right)
    # An observed null does not make the field optional; it makes it nullable.
    if types == {"integer", "number"}:
        types = {"number"}

    merged: dict[str, Any] = {
        "type": sorted(types)[0] if len(types) == 1 else sorted(types)
    }

    if "object" in types:
        left_props = left.get("properties") or {}
        right_props = right.get("properties") or {}
        properties: dict[str, Any] = {}
        for key in sorted(set(left_props) | set(right_props)):
            if key in left_props and key in right_props:
                properties[key] = merge(left_props[key], right_props[key])
            else:
                properties[key] = left_props.get(key) or right_props[key]
        merged["properties"] = properties
        # Required is the intersection: only what BOTH observations had is
        # guaranteed. This is where a schema learns which fields are optional.
        left_required = set(left.get("required") or ())
        right_required = set(right.get("required") or ())
        if "object" in _type_set(left) and "object" in _type_set(right):
            merged["required"] = sorted(left_required & right_required)
        else:
            merged["required"] = sorted(left_required | right_required)

    if "array" in types:
        left_items = left.get("items")
        right_items = right.get("items")
        if left_items and right_items:
            merged["items"] = merge(left_items, right_items)
        elif left_items or right_items:
            merged["items"] = left_items or right_items

    return merged


def _type_set(schema: dict[str, Any]) -> set[str]:
    declared = schema.get("type")
    if isinstance(declared, list):
        return {str(item) for item in declared}
    return {str(declared)} if declared else set()


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Every way ``value`` departs from ``schema``. Empty means it conforms.

    Returns all problems rather than the first, because a shape change usually
    breaks several fields at once and fixing them one round-trip at a time is
    the slow way to learn that.
    """
    problems: list[str] = []
    allowed = _type_set(schema)
    if not allowed:
        return problems

    actual = type_of(value)
    # An integer satisfies a `number` contract; the reverse is not true.
    if actual not in allowed and not (actual == "integer" and "number" in allowed):
        problems.append(f"{path}: expected {'/'.join(sorted(allowed))}, got {actual}")
        return problems

    if actual == "object":
        properties = schema.get("properties") or {}
        for key in schema.get("required") or ():
            if key not in value:
                problems.append(f"{path}.{key}: required key is missing")
        for key, item in sorted(value.items()):
            if key in properties:
                problems.extend(validate(item, properties[key], f"{path}.{key}"))
            else:
                # A new key is additive and safe for existing clients, so it is
                # reported as drift to re-freeze rather than as a violation.
                problems.append(f"{path}.{key}: not in the frozen shape")
    elif actual == "array":
        items = schema.get("items")
        if items:
            for index, item in enumerate(value):
                problems.extend(validate(item, items, f"{path}[{index}]"))
    return problems


__all__ = ["infer", "merge", "type_of", "validate"]
