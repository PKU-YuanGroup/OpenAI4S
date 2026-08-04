"""Language-neutral wire constants for one-shot shell capabilities.

This tiny module is imported by both the Host issuer and the kernel-side SDK.
Keeping it outside ``openai4s.host`` prevents a worker import from executing the
host service package's composition imports.
"""

from __future__ import annotations

import hashlib

CAPABILITY_VERSION = "openai4s-bash-capability-v1"


def command_digest(command: str) -> str:
    """Return the canonical SHA-256 binding for a shell command string."""

    return hashlib.sha256(command.encode("utf-8", errors="surrogatepass")).hexdigest()


__all__ = ["CAPABILITY_VERSION", "command_digest"]
