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

## Linux, and what "confined" is allowed to mean

The invariant is the **filesystem** one, on both platforms, by owner decision.
The helper's original Linux check was a fresh network namespace, which a helper
whose entire job is calling a provider API cannot have without a host egress
proxy in front of it. Rather than leave Linux unconfined waiting for that,
Linux gets the same boundary macOS has — `--tmpfs $HOME` with the interpreter's
own paths bound back over it — through bubblewrap.

Network isolation is therefore a **separate capability, and it is not enabled**.
That is not a detail to leave implicit: "the helper is confined" is read by most
people as "the helper cannot phone home", so `network_isolated()` exists as its
own question and `confinement_status()` answers it out loud. A boundary nobody
mentions is a boundary people assume.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Where the sandbox binary lives on macOS. Probed rather than assumed so a
#: stripped system reports unavailable instead of failing at exec time.
_SEATBELT = "sandbox-exec"

#: The Linux backend. Same filesystem invariant, different mechanism.
_BWRAP = "bwrap"


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


def build_bwrap_argv(
    argv: list[str],
    stage: str | os.PathLike[str],
    *,
    executable: str,
    home: str | os.PathLike[str] | None = None,
    read_paths: tuple[str, ...] = (),
) -> list[str]:
    """The Linux form of the same filesystem invariant.

    `--tmpfs $HOME` replaces the user's home with an empty filesystem, then the
    interpreter's own paths are bound back on top — bwrap applies mounts in
    order, so a later bind wins over the tmpfs beneath it. The result is the
    macOS profile's shape: the helper's stage is writable, the user's files are
    not there at all, and everything the interpreter needs to run is.

    **No `--unshare-net`.** Network isolation is a separate capability with its
    own decision behind it (a helper whose job is calling a provider API cannot
    live in an empty netns without a host egress proxy), so it is deliberately
    not claimed here — and ``confinement_status`` reports it as not isolated
    rather than staying quiet, because a boundary nobody mentions is one people
    assume.
    """
    home_dir = _canonical(home if home is not None else Path.home())
    stage_dir = _canonical(stage)
    wrapped = [
        str(executable),
        "--die-with-parent",
        "--new-session",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        # The invariant: the user's home is not the user's home in here.
        "--tmpfs",
        home_dir,
    ]
    for path in list(read_paths) or runtime_read_paths():
        # Bound back *after* the tmpfs so the interpreter can still start; a
        # read-only bind, because nothing here needs to write to them.
        wrapped.extend(["--ro-bind", path, path])
    wrapped.extend(["--bind", stage_dir, stage_dir, "--chdir", stage_dir, "--"])
    wrapped.extend(str(part) for part in argv)
    return wrapped


def network_isolated() -> bool:
    """Whether the boundary also isolates the network. It does not.

    Stated as its own question rather than folded into `available()`, because
    the two are separate capabilities and conflating them is how "the helper is
    confined" comes to be read as "the helper cannot reach the network".
    """
    return False


def available() -> tuple[bool, str]:
    """Whether a boundary can be established here, and why not when it cannot."""
    if sys.platform == "darwin":
        if shutil.which(_SEATBELT):
            return True, "macOS Seatbelt"
        return False, f"{_SEATBELT} is not on PATH"
    if sys.platform.startswith("linux"):
        if shutil.which(_BWRAP):
            return True, "Linux bubblewrap"
        return False, f"{_BWRAP} is not on PATH"
    return False, f"no confinement backend for platform {sys.platform!r}"


def probe_environment(home: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """What the confined helper needs from the host to verify the boundary.

    The helper checks from inside, and on Linux the filesystem invariant is not
    self-evident: an empty `$HOME` could be an empty home. So the host passes
    the device id of the *real* home, and a differing one inside means the
    tmpfs is in place. Same shape as the netns-inode anchor this replaces —
    the host supplies the value to compare against, because the confined
    process cannot obtain it.
    """
    home_dir = _canonical(home if home is not None else Path.home())
    try:
        return {"OPENAI4S_HOST_HOME_DEV": str(os.stat(home_dir).st_dev)}
    except OSError:
        return {}


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
    if sys.platform.startswith("linux"):
        executable = shutil.which(_BWRAP)
        if not executable:  # pragma: no cover - available() already checked
            raise ConfinementUnavailable(f"{_BWRAP} is not on PATH")
        return build_bwrap_argv(
            argv, stage, executable=executable, read_paths=read_paths
        )
    executable = shutil.which(_SEATBELT)
    if not executable:  # pragma: no cover - available() already checked
        raise ConfinementUnavailable(f"{_SEATBELT} is not on PATH")
    profile = build_profile(stage, read_paths=read_paths)
    return [executable, "-p", profile, *[str(part) for part in argv]]


__all__ = [
    "ConfinementUnavailable",
    "available",
    "build_bwrap_argv",
    "build_profile",
    "network_isolated",
    "probe_environment",
    "runtime_read_paths",
    "wrap",
]
