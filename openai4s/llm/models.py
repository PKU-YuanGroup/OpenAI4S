"""Shared provider-layer errors and lightweight types."""

from __future__ import annotations


class LLMError(RuntimeError):
    """Normalized failure raised by every LLM transport and provider."""
