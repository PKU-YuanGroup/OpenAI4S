"""The sole successful completion contract for Code-as-Action tasks."""

from __future__ import annotations

import re
from typing import Any

PAST_TENSE_STARTERS = frozenset(
    {
        "built",
        "found",
        "made",
        "ran",
        "wrote",
        "read",
        "sent",
        "set",
        "got",
        "began",
        "chose",
        "drew",
        "fit",
        "held",
        "kept",
        "led",
        "left",
        "put",
        "saw",
        "shown",
        "showed",
        "split",
        "taught",
        "told",
        "understood",
        "computed",
        "created",
        "generated",
        "produced",
        "analyzed",
        "identified",
    }
)
_CJK_START = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def first_english_word(bullet: Any) -> str | None:
    """The lowercased first word of an English bullet, or ``None``.

    ``None`` means the word-level heuristics do not apply: the bullet is not a
    non-empty string, or it starts with CJK text, whose morphology does not
    mark tense the way the English guards assume.
    """
    if not isinstance(bullet, str) or not bullet.strip():
        return None
    first = re.split(r"\s+", bullet.strip())[0].lower()
    return None if _CJK_START.match(first) else first


def validate_completion_bullets(bullets: list) -> str | None:
    """Require 1-4 non-empty completed-action bullets.

    English bullets retain the past-tense verb guard. CJK languages do not
    encode tense with the same morphology, so their non-empty verb phrases are
    accepted instead of being forced through an English suffix rule.
    """
    if not isinstance(bullets, list) or not (1 <= len(bullets) <= 4):
        return "completion_bullets must be a list of 1-4 items"
    for bullet in bullets:
        if not isinstance(bullet, str) or not bullet.strip():
            return "each completion bullet must be a non-empty string"
        first = first_english_word(bullet)
        if first is None:
            continue
        if not (first.endswith("ed") or first in PAST_TENSE_STARTERS):
            return (
                f"completion bullet {bullet!r} must start with a past-tense verb "
                f"(e.g. 'Computed...', 'Saved...')"
            )
    return None


def validate_output_schema(output: Any, schema: dict) -> str | None:
    """Apply the legacy minimal JSON-schema-like output validation."""
    if not isinstance(schema, dict):
        return None
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(output, dict):
            return "output must be an object per output_schema"
        for required in schema.get("required", []):
            if required not in output:
                return f"output missing required field {required!r}"
    elif schema_type == "array" and not isinstance(output, list):
        return "output must be an array per output_schema"
    elif schema_type == "string" and not isinstance(output, str):
        return "output must be a string per output_schema"
    elif schema_type == "number" and not isinstance(output, (int, float)):
        return "output must be a number per output_schema"
    return None


class CompletionService:
    """Validate and commit the one terminal signal accepted from a cell.

    Prose never completes a task.  A successful :meth:`submit` stores the
    structured output that the outer Agent/Gateway loop observes after cell
    execution.  Validation failures are soft errors and leave the prior state
    untouched, so the model can recover in a later cell.
    """

    def __init__(self) -> None:
        self.last_output: dict | None = None

    def submit(self, spec: dict) -> dict:
        bullets = spec.get("completion_bullets") or []
        error = validate_completion_bullets(bullets)
        if error:
            return {"error": error}

        schema = spec.get("output_schema")
        if schema is not None:
            error = validate_output_schema(spec.get("output"), schema)
            if error:
                return {"error": error}

        self.last_output = {
            "output": spec.get("output"),
            "completion_bullets": bullets,
        }
        return {"status": "ok"}

    def clear(self) -> None:
        self.last_output = None


__all__ = [
    "CompletionService",
    "PAST_TENSE_STARTERS",
    "first_english_word",
    "validate_completion_bullets",
    "validate_output_schema",
]
