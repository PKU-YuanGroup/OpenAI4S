"""Offline providers used by deterministic harness scenarios."""

from .scripted_llm import ScriptedLLM, ScriptedProviderError

__all__ = ["ScriptedLLM", "ScriptedProviderError", "start_fake"]


def __getattr__(name: str) -> object:
    # Lazy so ``python -m harness.providers.typesafe_fake`` is not a double import.
    if name == "start_fake":
        from .typesafe_fake import start_fake

        return start_fake
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
