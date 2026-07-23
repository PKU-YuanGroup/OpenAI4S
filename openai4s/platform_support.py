"""Which platforms this program runs on, declared in one place.

The frozen platform matrix (docs/v02-decisions.md, 8.5) is: macOS arm64 stable,
Linux server/browser beta, **Windows unsupported and fails closed**.

"Fails closed" is the load-bearing half, and it is not what the code did. A
native Windows install printed one line during onboarding -- "Native Windows
kernels are unsupported; run OpenAI4S under WSL2" -- and then went on to try to
start a kernel anyway. A program that warns and proceeds has made a different
promise from one that refuses: the first leaves a scientist to discover the
problem from a half-working analysis, and this product's whole claim is that
its results can be trusted.

So the refusal lives at the one place every kernel must pass through, and the
platform vocabulary lives here rather than in scattered `sys.platform` string
comparisons, so that "what do we support" has an answer a test can read.

Deliberately not a general capability framework. It answers one question --
may a kernel start on this platform -- because that is the question the frozen
decision asks, and a wider abstraction would invite adding a platform by
editing a table rather than by doing the work that makes it supported.
"""
from __future__ import annotations

import sys

#: Platforms a kernel may start on. Keyed by the `sys.platform` prefix, because
#: that is what the interpreter actually reports; `platform.system()` is a
#: different vocabulary and mixing the two is how a check silently stops
#: matching.
SUPPORTED_PREFIXES = ("darwin", "linux")

#: Windows is named rather than left to the default so the message can say what
#: to do instead. WSL2 reports `linux`, so a user following this advice lands in
#: the supported set.
WINDOWS_PREFIX = "win"

_WINDOWS_GUIDANCE = (
    "Native Windows is not supported: the kernel spawns POSIX subprocesses and "
    "the sandbox has no Windows backend, so a kernel started here would run "
    "unisolated and behave differently from every other install. Run OpenAI4S "
    "under WSL2, which reports as Linux and is supported."
)


class UnsupportedPlatform(RuntimeError):
    """Raised instead of starting a kernel on a platform we do not support."""


def platform_name(value: str | None = None) -> str:
    """The platform tag, taken from `sys.platform` unless one is supplied."""
    return (value if value is not None else sys.platform).lower()


def is_supported(value: str | None = None) -> bool:
    return platform_name(value).startswith(SUPPORTED_PREFIXES)


def support_status(value: str | None = None) -> str:
    """`stable`, `beta`, or `unsupported`, per the frozen matrix.

    macOS is stable and Linux is beta because Linux's tier is gated on the
    enforced-bubblewrap smoke test, not because the code differs.
    """
    name = platform_name(value)
    if name.startswith("darwin"):
        return "stable"
    if name.startswith("linux"):
        return "beta"
    return "unsupported"


def require_supported(value: str | None = None) -> None:
    """Refuse to continue on an unsupported platform.

    Called on the kernel spawn path, which every Python and R kernel passes
    through, so there is no route that reaches a subprocess without this having
    been asked.
    """
    name = platform_name(value)
    if is_supported(name):
        return
    if name.startswith(WINDOWS_PREFIX):
        raise UnsupportedPlatform(_WINDOWS_GUIDANCE)
    raise UnsupportedPlatform(
        f"platform {name!r} is not supported: kernels require a POSIX platform "
        "with a sandbox backend. Supported: macOS (stable), Linux (beta)."
    )


__all__ = [
    "SUPPORTED_PREFIXES",
    "UnsupportedPlatform",
    "is_supported",
    "platform_name",
    "require_supported",
    "support_status",
]
