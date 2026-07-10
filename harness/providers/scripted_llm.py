"""A queue-backed model provider with no network or production dependency."""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping, Sequence

from ..schema import ProviderStep


class ScriptedProviderError(RuntimeError):
    # A plain exception subclass (not a dataclass): BaseException.args stays
    # populated for logging/pickle, and identity equality/hash are preserved.
    def __init__(
        self,
        kind: str,
        message: str,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(kind, message, status, headers, retryable)
        self.kind = kind
        self.message = message
        self.status = status
        self.headers = headers
        self.retryable = retryable

    def __str__(self) -> str:
        return self.message


class ScriptedLLM:
    """Return predeclared responses in order and retain immutable call records."""

    def __init__(self, steps: Iterable[ProviderStep]):
        self._steps = list(steps)
        self._cursor = 0
        self.calls: list[list[dict[str, Any]]] = []
        self.last_step: ProviderStep | None = None

    @property
    def remaining(self) -> int:
        return len(self._steps) - self._cursor

    def __call__(
        self,
        messages: Sequence[Mapping[str, Any]],
        _cfg: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if self._cursor >= len(self._steps):
            raise AssertionError("scripted provider exhausted")
        self.calls.append(copy.deepcopy([dict(message) for message in messages]))
        step = self._steps[self._cursor]
        self._cursor += 1
        self.last_step = step
        if step.error is not None:
            raw = step.error
            status = raw.get("status")
            if status is not None and (
                not isinstance(status, int) or isinstance(status, bool)
            ):
                raise AssertionError("scripted error status must be an integer")
            headers_raw = raw.get("headers")
            headers = None
            if headers_raw is not None:
                if not isinstance(headers_raw, Mapping):
                    raise AssertionError("scripted error headers must be an object")
                headers = {str(key): str(value) for key, value in headers_raw.items()}
            raise ScriptedProviderError(
                kind=str(raw["kind"]),
                message=str(raw["message"]),
                status=status,
                headers=headers,
                retryable=bool(raw.get("retryable", False)),
            )
        response = copy.deepcopy(dict(step.response or {}))
        response.setdefault("reasoning", None)
        response.setdefault("usage", {})
        response.setdefault("finish_reason", "stop")
        response.setdefault("raw", {})
        return response
