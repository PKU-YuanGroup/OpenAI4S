"""Shared MCP protocol limits and typed transport failures.

Both the stdio and Streamable HTTP transports speak JSON-RPC and expose the
same failure categories to their callers.  Keeping those small definitions in
one focused module avoids making either transport depend on the other's
private implementation details.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Largest single JSON-RPC frame or HTTP response body accepted by an MCP
#: transport.  The bound is expressed in bytes because bytes are what arrive
#: from both a subprocess pipe and an HTTP socket.
MAX_FRAME_BYTES = 4 * 1024 * 1024


class MCPError(RuntimeError):
    """Base failure reported by an MCP transport or protocol operation."""


class MCPTimeout(MCPError):
    """A request outlived its absolute deadline."""


class MCPOversizedResponse(MCPError):
    """The connector answered, but its response exceeded the accepted bound."""

    error_code = "response_too_large"


def redact_reflected_secret(value: Any, secret: str) -> Any:
    """Recursively scrub one reflected secret without dropping collisions.

    A secret used as a JSON property name can redact to a name that the
    upstream already returned. Ordinary dict assignment would silently replace
    one of those fields. Stable ``#N`` suffixes preserve every value without
    deriving a public key name from the credential.
    """

    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, (list, tuple)):
        return [redact_reflected_secret(item, secret) for item in value]
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            # Credential validation rejects control characters and excessive
            # length, but intentionally does not assume a provider-specific
            # minimum. A short key can still be real, so it must be removed
            # when embedded in an untrusted JSON property name too. Product
            # adapters rebuild their small trusted protocol skeleton after
            # this transformation.
            text = str(key).replace(secret, "[REDACTED]")
            candidate = text
            suffix = 2
            while candidate in safe:
                candidate = f"{text}#{suffix}"
                suffix += 1
            safe[candidate] = redact_reflected_secret(item, secret)
        return safe
    return value


__all__ = [
    "MAX_FRAME_BYTES",
    "MCPError",
    "MCPOversizedResponse",
    "MCPTimeout",
    "redact_reflected_secret",
]
