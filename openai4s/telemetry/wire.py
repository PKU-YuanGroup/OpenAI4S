"""Turning records into bytes, and the only place that may.

Every byte telemetry sends is produced here, by `seal()`. The sender takes a
`SealedPayload` and cannot build one: its constructor requires a sentinel this
module never exports, so no other file in the tree can hand the transport a
payload that skipped validation. That is the whole point of the type existing --
without it, "the sender only sends sanitised data" is a convention, and a
convention is what the next contributor is unaware of.

Validation is not a filter applied to a dict someone else assembled. The
envelope is built here, field by field, from the declaration in `schema.py`.
Nothing is copied through.
"""
from __future__ import annotations

import json
import platform
import sys
from typing import Any

from openai4s.telemetry import schema

#: Only `seal` holds this. `SealedPayload(...)` from anywhere else is a
#: TypeError, so the transport cannot be handed unvalidated bytes.
_SEAL = object()


class SealedPayload:
    """Bytes that have been through the declaration. Construct via `seal`."""

    __slots__ = ("body", "record_count")

    def __init__(self, token: object, body: bytes, record_count: int) -> None:
        if token is not _SEAL:
            raise TypeError(
                "SealedPayload is built by openai4s.telemetry.wire.seal(); "
                "constructing one elsewhere would let unvalidated bytes reach "
                "the transport"
            )
        self.body = body
        self.record_count = record_count


def _os_name() -> str:
    name = sys.platform
    if name.startswith("darwin"):
        return "darwin"
    if name.startswith("linux"):
        return "linux"
    if name.startswith("win"):
        return "windows"
    return "other"


def _arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    return "other"


def _python() -> str:
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return version if version in {"3.10", "3.11", "3.12", "3.13"} else "other"


def seal(install_id: str, records: list[dict[str, Any]]) -> SealedPayload | None:
    """Build the one payload shape telemetry can send, or None if there is none.

    Returns None rather than an empty envelope when nothing survived
    sanitising: sending "I have nothing to say" is still a packet, and still
    tells a listener that this install is running right now.
    """
    from openai4s import __version__

    clean = [r for r in (schema.sanitise_record(rec) for rec in records) if r]
    if not clean:
        return None
    clean = clean[: schema.MAX_RECORDS]

    envelope = schema.sanitise_envelope(
        {
            "schema": schema.SCHEMA_VERSION,
            "install_id": install_id,
            "app_version": __version__,
            "os": _os_name(),
            "arch": _arch(),
            "python": _python(),
        }
    )
    # An envelope missing its identity or its schema version is not something
    # to send a best-effort version of.
    if "install_id" not in envelope or "schema" not in envelope:
        return None

    envelope["events"] = clean
    body = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return SealedPayload(_SEAL, body, len(clean))


__all__ = ["SealedPayload", "seal"]
