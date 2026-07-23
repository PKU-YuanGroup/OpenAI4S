"""An OS boundary around the BYOC provider helper.

The helper was designed to be confined and shipped unconfined. It carries a
self-check (`expect_confined`) and an exit code for failing it (71), and the
host never wrapped it in anything — so `confinement_status()` reported
`enforced: False` and `OPENAI4S_COMPUTE_CONFINEMENT=enforce` refused every op
rather than establishing the boundary it demanded. That was honest, and it was
still a designed-but-not-built boundary.

## What the helper's own probe demands

The check lives in the helper because a boundary the *host* asserts is a claim;
a boundary the confined process verifies from the inside is evidence. On macOS
it probes `listdir($HOME)` and expects `PermissionError`. That is the invariant
this module has to produce.

## Why the profile is shaped the way it is

The helper is not a kernel cell, and the kernel's profile is wrong for it in
both directions:

* it **must** reach the network — its whole job is calling a provider's REST
  API — where a cell must not;
* it must **not** read the user's home, where a cell legitimately may (science
  reads data files).

So: `allow default`, writes confined to the stage directory, network allowed,
and `$HOME` unreadable — with the specific paths the interpreter needs to run
at all read-allowed again on top, because SBPL is last-match-wins. Those paths
are the Python installation and the helper's own package tree, which on a
developer machine live under `$HOME`. Allow-listing them is not a hole in the
boundary being built: the boundary is "the user's documents, credentials, ssh
keys and shell history are not readable", and an interpreter that cannot import
itself confines nothing because it never runs.

## Linux

Not implemented, and deliberately not faked. The helper's Linux invariant is a
*fresh network namespace* — which is incompatible with a helper whose purpose
is to reach a provider API over that network. Reconciling the two needs a
decision that is not this module's to make: either egress moves behind a host
proxy so the helper really can live in an empty netns, or the Linux invariant
becomes a filesystem one to match macOS. Until that is decided, Linux reports
`unavailable` with the reason, `auto` runs unconfined and says so, and
`enforce` refuses — the same posture as before, but now for a stated reason
rather than for lack of any implementation at all.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Where the sandbox binary lives on macOS. Probed rather than assumed so a
#: stripped system reports unavailable instead of failing at exec time.
_SEATBELT = "sandbox-exec"


class ConfinementUnavailable(RuntimeError):
    """No OS boundary can be established for the helper on this host."""


def _quote(value: str | os.PathLike[str]) -> str:
    text = str(value)
    if "\x00" in text:
        raise ConfinementUnavailable("a confined path cannot contain a NUL byte")
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _canonical(path: str | os.PathLike[str]) -> str:
    """The path Seatbelt will actually match against.

    macOS puts temp directories under ``/var/folders/...`` where ``/var`` is a
    symlink to ``/private/var``. The kernel resolves before it evaluates the
    profile, so a rule written with the unresolved path matches nothing — the
    stage directory ends up unwritable and the helper cannot answer at all.
    """
    return os.path.realpath(str(path))


def runtime_read_paths(extra: tuple[str, ...] = ()) -> list[str]:
    """Directories the helper must be able to read to exist as a process.

    The interpreter, its standard library and site-packages, and the helper's
    own package tree. Anything else under the home directory stays denied.
    """
    candidates = [
        sys.prefix,
        sys.base_prefix,
        # The helper package and the provider shim it loads.
        str(Path(__file__).resolve().parent.parent.parent),
        *extra,
    ]
    seen: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or not Path(text).exists():
            continue
        resolved = _canonical(text)
        if resolved not in seen:
            seen.append(resolved)
    return seen


def build_profile(
    stage: str | os.PathLike[str],
    *,
    home: str | os.PathLike[str] | None = None,
    read_paths: tuple[str, ...] = (),
) -> str:
    """The Seatbelt profile for one helper invocation.

    Order is load-bearing — SBPL takes the *last* matching rule — so the home
    denial comes after `allow default`, and the runtime read allowances come
    after the denial.
    """
    home_dir = _canonical(home if home is not None else Path.home())
    stage_dir = _canonical(stage)
    lines = [
        "(version 1)",
        "(allow default)",
        # Writes: the stage directory is the helper's entire output surface
        # (req.json in, reply.json and sandbox_id out, archives through).
        "(deny file-write*)",
        "(allow file-write*",
        f"    (subpath {_quote(stage_dir)})",
        '    (literal "/dev/null")',
        '    (literal "/dev/zero")',
        '    (literal "/dev/stdout")',
        '    (literal "/dev/stderr"))',
        # The invariant the helper verifies from inside.
        #
        # `file-read-data`, not `file-read*`. Denying the whole read class over
        # a home directory also denies the *metadata* reads `execvp` and dyld
        # perform on the interpreter itself, so `sandbox-exec` fails before the
        # helper starts — verified by execution: with `file-read*` the exec
        # dies with "Operation not permitted", with `file-read-data` the helper
        # runs and `os.listdir($HOME)` still raises PermissionError, which is
        # exactly the invariant the helper probes for. Metadata is what the
        # loader needs; contents are what a credential is.
        f"(deny file-read-data (subpath {_quote(home_dir)}))",
    ]
    allowed = list(read_paths) or runtime_read_paths()
    if allowed:
        lines.append("(allow file-read-data")
        lines.extend(f"    (subpath {_quote(path)})" for path in allowed)
        lines.append(f"    (subpath {_quote(stage_dir)}))")
    return "\n".join(lines) + "\n"


def available() -> tuple[bool, str]:
    """Whether a boundary can be established here, and why not when it cannot."""
    if sys.platform == "darwin":
        if shutil.which(_SEATBELT):
            return True, "macOS Seatbelt"
        return False, f"{_SEATBELT} is not on PATH"
    if sys.platform.startswith("linux"):
        return False, (
            "the helper's Linux confinement invariant is a fresh network "
            "namespace, which a helper that must reach a provider API cannot "
            "have; reconciling the two is an open decision (host egress proxy, "
            "or a filesystem invariant matching macOS)"
        )
    return False, f"no confinement backend for platform {sys.platform!r}"


def wrap(
    argv: list[str],
    stage: str | os.PathLike[str],
    *,
    read_paths: tuple[str, ...] = (),
) -> list[str]:
    """Return ``argv`` wrapped in the boundary, or raise if there is none."""
    ok, reason = available()
    if not ok:
        raise ConfinementUnavailable(reason)
    executable = shutil.which(_SEATBELT)
    if not executable:  # pragma: no cover - available() already checked
        raise ConfinementUnavailable(f"{_SEATBELT} is not on PATH")
    profile = build_profile(stage, read_paths=read_paths)
    return [executable, "-p", profile, *[str(part) for part in argv]]


__all__ = [
    "ConfinementUnavailable",
    "available",
    "build_profile",
    "runtime_read_paths",
    "wrap",
]
