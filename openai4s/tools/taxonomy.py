"""Stable side-effect and resource-key vocabulary for control tools."""

from __future__ import annotations

import posixpath
import re
from typing import Any

READ_ONLY = "read_only"
WORKSPACE_WRITE = "workspace_write"
RUNTIME_MUTATION = "runtime_mutation"
EXTERNAL_WRITE = "external_write"
HIGH_RISK = "high_risk"

SIDE_EFFECT_CLASSES = frozenset(
    {READ_ONLY, WORKSPACE_WRITE, RUNTIME_MUTATION, EXTERNAL_WRITE, HIGH_RISK}
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE = re.compile(r"\s+")


def resource_key(namespace: str, target: Any = "") -> str:
    """Build a bounded, log-safe ``namespace:target`` resource identifier."""
    clean_namespace = _component(namespace, fallback="tool")
    clean_target = _component(target, fallback="*")
    return f"{clean_namespace}:{clean_target}"


def workspace_target(value: Any) -> str:
    """Normalize a model-provided relative path without touching the filesystem."""
    raw = str(value or ".").replace("\\", "/")
    normalized = posixpath.normpath(raw)
    return "." if normalized in ("", ".") else normalized


def _component(value: Any, *, fallback: str) -> str:
    text = _CONTROL_CHARS.sub(" ", str(value or "")).strip()
    text = _SPACE.sub(" ", text).replace(":", "%3A")
    if not text:
        text = fallback
    if len(text) > 512:
        text = text[:509] + "..."
    return text


__all__ = [
    "READ_ONLY",
    "WORKSPACE_WRITE",
    "RUNTIME_MUTATION",
    "EXTERNAL_WRITE",
    "HIGH_RISK",
    "SIDE_EFFECT_CLASSES",
    "resource_key",
    "workspace_target",
]
