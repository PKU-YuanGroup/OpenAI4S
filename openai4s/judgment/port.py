"""Replaceable judgment backend port. W0 ships only NullBackend."""

from __future__ import annotations

from typing import Literal, Mapping, Protocol

from openai4s.judgment.types import BackendReply, Question

BackendErrorCode = Literal[
    "disabled",
    "unconfigured",
    "auth",
    "invalid_request",
    "rate_limited",
    "overloaded",
    "timeout",
    "invalid_response",
    "egress_blocked",
    "unavailable",
]

BACKEND_ERROR_CODES: frozenset[str] = frozenset(
    (
        "disabled",
        "unconfigured",
        "auth",
        "invalid_request",
        "rate_limited",
        "overloaded",
        "timeout",
        "invalid_response",
        "egress_blocked",
        "unavailable",
    )
)


class BackendError(Exception):
    """Controlled backend failure. Callers must not invent default scores."""

    def __init__(self, code: str, message: str = "") -> None:
        if code not in BACKEND_ERROR_CODES:
            raise ValueError(f"invalid backend error code: {code!r}")
        self.code: str = code
        self.message: str = message
        super().__init__(message or code)


class JudgmentBackend(Protocol):
    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply: ...


class NullBackend:
    """Always-disabled backend used when the experimental feature is off."""

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        del state, questions, model, timeout
        raise BackendError("disabled", "experimental judgment is disabled")
