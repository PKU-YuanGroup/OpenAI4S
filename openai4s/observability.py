"""Correlation IDs and structured, redacted logging.

The daemon logged with bare ``print()`` and carried no request identity at all,
so a user reporting "my run failed" could not be tied to the HTTP request, the
session, the execution, or the remote job it became. Support meant guessing from
timestamps.

Two pieces:

* a **correlation id** carried in a ``ContextVar``, so any code reached from a
  request — including a background thread it spawns — can stamp its output with
  the same id without every function growing a parameter; and
* a **structured emitter** that writes one JSON object per line with that id
  attached, and refuses to serialize anything that looks like a credential.

## Redaction is deny-by-default, not a denylist

The proposal is explicit that logs, diagnostics, and exports must contain no
secret material, and that a denylist is not evidence of that. So this does not
try to spot secrets by name. It emits only fields a caller passed explicitly,
and it scrubs any *value* that looks like a credential — a broker reference's
target, a long opaque token, an Authorization header — wherever it appears,
including nested. A field whose value cannot be shown is replaced by a
fingerprint, so two log lines can still be correlated to the same secret without
either revealing it.

Prompts and research data are never logged by this module. There is no
`log_prompt`; the model's messages and the kernel's data are the two things most
likely to carry a user's unpublished work, and the safe default is that they do
not leave the process through this path at all.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import sys
import time
import uuid
from typing import Any

# The id for the unit of work currently in flight. A ContextVar rather than a
# thread-local because the gateway hands requests to threads *and* the value has
# to survive into anything those threads schedule.
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "openai4s_correlation_id", default=""
)

# Off unless asked for. Structured logs are an operator tool; turning them on by
# default would change what every existing deployment writes to disk.
_ENABLED_ENV = "OPENAI4S_STRUCTURED_LOGS"

# Values at or above this length that look opaque are treated as credential
# material. Chosen to sit above ordinary identifiers (a uuid4 hex is 32) and at
# or below real keys (sk-... tokens run 40+).
_OPAQUE_MIN = 24

_SENSITIVE_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "env",
    "password",
    "secret",
    "token",
)


def new_correlation_id() -> str:
    """A fresh id. Short enough to eyeball in a log, wide enough not to collide."""
    return uuid.uuid4().hex[:16]


def correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> contextvars.Token:
    return _correlation_id.set(str(value or ""))


def reset_correlation_id(token: contextvars.Token) -> None:
    try:
        _correlation_id.reset(token)
    except ValueError:
        # The token belongs to a different context (the caller crossed a thread
        # boundary). Losing the reset is harmless; the context dies with it.
        pass


def fingerprint(value: str) -> str:
    """A stable, non-reversible tag so two lines can be tied to one secret."""
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:12]


def _looks_opaque(text: str) -> bool:
    """A long run of credential-shaped characters and nothing else.

    Deliberately shape-based rather than name-based: a secret stored under an
    unremarkable key is exactly the one a name rule misses.
    """
    if len(text) < _OPAQUE_MIN:
        return False
    if " " in text or "\n" in text:
        return False
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+/=:"
    )
    if not set(text) <= allowed:
        return False
    # A path or URL is long and opaque-looking but is not a credential, and
    # redacting it would make the log useless for the thing it is for.
    if text.startswith(("/", "./", "http://", "https://", "file://")):
        return False
    # Must actually mix character classes; "aaaaaaaa..." is not a key.
    return any(c.isdigit() for c in text) and any(c.isalpha() for c in text)


def redact(value: Any, *, _key: str = "", _depth: int = 0) -> Any:
    """Return `value` with credential material replaced by a fingerprint."""
    if _depth > 6:
        return "<too-deep>"
    if isinstance(value, dict):
        return {k: redact(v, _key=str(k), _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, _key=_key, _depth=_depth + 1) for v in value]
    if not isinstance(value, str):
        return value

    key_is_sensitive = any(s in _key.lower() for s in _SENSITIVE_KEYS)
    # A broker reference is not itself a secret, but it names one; keep it,
    # since its whole purpose is to be safe to record.
    if value.startswith("secret://"):
        return value
    if key_is_sensitive and value:
        return f"<redacted:{fingerprint(value)}>"
    if _looks_opaque(value):
        return f"<redacted:{fingerprint(value)}>"
    return value


def enabled() -> bool:
    return os.environ.get(_ENABLED_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def log_event(event: str, /, **fields: Any) -> dict:
    """Emit one structured line and return what was emitted.

    Returns the record even when logging is disabled so a caller can assert on
    the redaction without turning logging on for the whole test suite.
    """
    record = {
        "ts": round(time.time(), 3),
        "event": str(event),
        "correlation_id": correlation_id(),
    }
    record.update({k: redact(v, _key=k) for k, v in fields.items()})
    if enabled():
        try:
            sys.stderr.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001 - logging must never break a request
            pass
    return record


__all__ = [
    "correlation_id",
    "enabled",
    "fingerprint",
    "log_event",
    "new_correlation_id",
    "redact",
    "reset_correlation_id",
    "set_correlation_id",
]
