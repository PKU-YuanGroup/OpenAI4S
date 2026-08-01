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
import functools
import hashlib
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# The id for the unit of work currently in flight. A ContextVar rather than a
# thread-local because within one thread it survives into everything that thread
# calls without any of it taking an id parameter.
#
# It does *not* cross a thread by itself -- a new `threading.Thread` starts with
# an empty context -- and the comment here used to claim it did. See
# `carry_context` below, which is what actually carries it, and which the
# request-serving spawn sites go through.
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


def carry_context(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``fn`` so a thread running it sees the *caller's* context.

    The comment above ``_correlation_id`` used to say a ContextVar was chosen
    "because the gateway hands requests to threads *and* the value has to
    survive into anything those threads schedule". That is precisely what a
    ContextVar does not do: a new ``threading.Thread`` starts with an empty
    context, so the id set while serving the request was gone the moment the
    work moved to a job thread -- which is where the slow, failure-prone work
    happens and where an id is worth having. Every structured log line emitted
    from a turn, a plan or a REPL job carried an empty ``request_id``, so the
    id a user quotes from a failed request matched nothing in the log for the
    work that actually failed.

    Capture happens here, on the spawning thread, at spawn time. Each call
    copies a fresh context, so two jobs started from two requests never share
    one and neither can reset the other's.

    Deliberately *not* applied to daemon-lifetime threads -- the idle sweeper,
    the share sweeper, the WebSocket drain, the MCP readers. Those are not
    serving the request that happened to start them, and stamping every later
    sweep with that request's id would be a false attribution, which is worse
    than a missing one because it gets believed.
    """
    context = contextvars.copy_context()
    return functools.partial(context.run, fn)


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


def redact_text(text: str) -> str:
    """Redact credential-shaped *tokens inside free text*.

    `redact` works on field values: it asks whether a whole string is a
    credential. That is the wrong question for a log line, where a key sits in
    the middle of a sentence — the surrounding spaces alone make the whole
    string "not opaque", so a stray `print(token)` sails through untouched.
    This scans word by word instead.

    Needed specifically for the diagnostic bundle: a line some future code
    emits without knowing about redaction still has to be safe to share, and
    "the author should have used the structured logger" is not a control.
    """
    out = []
    for word in str(text).split(" "):
        # Punctuation commonly abuts a token in prose ("key=sk-…," / "(sk-…)").
        stripped = word.strip("\"'`,;:()[]{}<>")
        if stripped.startswith(("http://", "https://", "ws://", "wss://")):
            # A URL has no spaces, so word-scanning sees the whole thing at
            # once — and `_looks_opaque` deliberately answers False for it,
            # because redacting every URL would gut the log. The credential is
            # *inside*, in a query value or a path segment, so it needs the
            # structural pass instead. The daemon's own startup banner is this
            # exact shape: `listening at http://127.0.0.1:8760/?token=…`,
            # printed to stdout, which every packaged launcher redirects into
            # the file the support bundle collects.
            out.append(word.replace(stripped, redact_url(stripped)))
        elif stripped and _looks_opaque(stripped):
            out.append(word.replace(stripped, f"<redacted:{fingerprint(stripped)}>"))
        else:
            out.append(word)
    return " ".join(out)


#: A home directory by *shape*, whoever owns it. `str.replace($HOME, "~")`
#: only ever sees this process's own, and a diagnostic bundle is shipped to
#: someone else: a path under a collaborator's home, a shared machine or a
#: mounted volume names a person exactly as squarely.
_HOME_SHAPED = re.compile(
    r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^/\\\s:\"'`,;()\[\]{}<>]+",
    re.IGNORECASE,
)
#: An account on a machine. Deliberately not "a shell command": there is no
#: boundary between a command quoted inside a failure message and the rest of
#: that message, so a rule wide enough to remove one removes the description
#: the log exists to carry. The identity inside it is separable, and is the
#: part that is worth removing.
#:
#: The right-hand side has to look like a host and not merely like text after
#: an `@`, or this eats `pkg@1.2.3` — a package spec, which the daemon logs on
#: every environment build and which is exactly the sort of line someone opens
#: a bundle to read. Address, or dotted name ending in a TLD, or `localhost`.
#: Residual, stated rather than hidden: a bare single-label host (`user@build`)
#: does not match, because nothing separates it from the false positives.
_USER_AT_HOST = re.compile(
    r"\b[\w.+-]+@(?:"
    r"(?:\d{1,3}\.){3}\d{1,3}"
    r"|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
    r"|localhost"
    r")\b"
)


#: Query parameters that hold credentials on real scientific APIs. Matched by
#: substring so `apikey`, `api_key` and `X-Api-Key` all land.
CREDENTIAL_PARAMS = ("key", "token", "secret", "password", "auth", "signature", "sig")


def _redact_path(path: str) -> str:
    """Fingerprint any path segment that looks like a credential.

    `redact_text` splits on spaces, and a URL has none — so a key embedded in
    the path (`/v1/sk-live-.../records`) arrives as a single "word" whose
    slashes and dots stop it reading as opaque, and it survives untouched. That
    is not hypothetical: path-style keys are ordinary in scientific APIs, and
    the test for it is what found this. Segments are the right unit because
    each one is exactly the kind of token the opacity check was written for.
    """
    return "/".join(
        f"<redacted:{fingerprint(segment)}>" if _looks_opaque(segment) else segment
        for segment in path.split("/")
    )


def _redact_netloc(netloc: str) -> str:
    """Userinfo is a credential by definition when it has a password."""
    if "@" not in netloc:
        return netloc
    userinfo, _, host = netloc.rpartition("@")
    user, sep, secret = userinfo.partition(":")
    if sep and secret:
        return f"{user}:<redacted:{fingerprint(secret)}>@{host}"
    if _looks_opaque(userinfo):
        return f"<redacted:{fingerprint(userinfo)}>@{host}"
    return netloc


def redact_url(raw: str) -> str:
    """Return the URL with credential-bearing query parameters fingerprinted.

    The parameter is kept and its *value* replaced, because "which parameters
    were sent" is provenance and the value is the secret. Dropping the
    parameter entirely would quietly change what the URL claims to have been.
    """
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<unparseable url>"
    # No early return for a URL without a query. An earlier version had one,
    # and it meant the path and userinfo redaction below never ran for exactly
    # the URLs that carry a path-style key -- which is the shape that has no
    # query by construction. The test for it is what found that.
    cleaned = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if value and any(bit in name.lower() for bit in CREDENTIAL_PARAMS):
            cleaned.append((name, f"<redacted:{fingerprint(value)}>"))
        else:
            cleaned.append((name, value))
    rebuilt = urlunsplit(
        (
            parts.scheme,
            _redact_netloc(parts.netloc),
            _redact_path(parts.path),
            urlencode(cleaned),
            parts.fragment,
        )
    )
    return rebuilt


def redact_identities(text: str) -> str:
    """Collapse who and where, keeping what.

    Paths keep everything but the home segment, because the file name is what
    makes the line worth reading and the user name is what identifies a person
    — the same trade `_redacted_detail` already makes for this account's own
    home, applied to the shape rather than to one literal string.

    Run this *after* `redact_text`: the fingerprints it leaves behind contain
    no `@` and no home-shaped prefix, so the two do not interfere.
    """
    out = _HOME_SHAPED.sub("~", str(text))
    return _USER_AT_HOST.sub(lambda m: f"<redacted:{fingerprint(m.group(0))}>", out)


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
    "CREDENTIAL_PARAMS",
    "redact",
    "redact_identities",
    "redact_text",
    "redact_url",
    "reset_correlation_id",
    "set_correlation_id",
]
