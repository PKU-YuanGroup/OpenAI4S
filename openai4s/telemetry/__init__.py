"""Opt-in, anonymous telemetry: counts and enumerations, never free text.

Off by default. With no consent recorded, nothing in this package opens a
socket, resolves a name, or starts a thread.

The package is deliberately small and deliberately boring. `schema.py` holds
the declaration of everything telemetry may say; the enforcement is over
*values*, not keys, because a key allowlist admits
`{"error_type": "FileNotFoundError: /home/y/unpublished/cohort.csv"}` exactly
as readily as `{"error_type": "ValueError"}`.
"""
from __future__ import annotations

from openai4s.telemetry.schema import (
    SCHEMA_VERSION,
    classify_error,
    sanitise_envelope,
    sanitise_record,
)

__all__ = [
    "SCHEMA_VERSION",
    "classify_error",
    "sanitise_envelope",
    "sanitise_record",
]
