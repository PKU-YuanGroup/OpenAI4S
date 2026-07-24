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

## Why an unreadable home is not enough on macOS

The credential this boundary exists to protect is not, in general, a file. The
secret broker stores it in the login keychain, and `security
find-generic-password -w` does not read `~/Library/Keychains` — it asks
*securityd* to, in a process the profile does not cover. `allow default` left
every Mach service that reaches securityd open, so the file denial the helper
verifies from inside was satisfied while the secret was still one command away,
on a helper that has the network by design. The profile therefore denies the
keychain services explicitly, and the self-test probes that separately from the
filesystem invariant, because neither implies the other.

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
import subprocess
import sys
import tempfile
import threading
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


def helper_package_dir() -> str:
    """The confined helper's own package directory — that directory alone.

    This used to be its *parent*, which in a source or editable install is the
    repository root. The home denial was then reopened over everything beneath
    it: an untracked ``.env``, ``.git`` and its objects, unrelated source and
    data — all readable by a provider shim that, by design, also has the
    network. The boundary was "the user's credentials are not reachable", and
    the allowance handed back the file most likely to hold them.

    The parent was on the list because ``__main__.py`` put it on ``sys.path``
    to ``import openai4s_compute_provider``, and a package import lists the
    directory it searches. The entrypoint now loads its own package by file
    location instead, so nothing above this directory has to be readable.
    """
    return str(
        Path(__file__).resolve().parent.parent.parent / "openai4s_compute_provider"
    )


def runtime_read_paths(extra: tuple[str, ...] = ()) -> list[str]:
    """Directories the helper must be able to read to exist as a process.

    The interpreter, its standard library and site-packages, the helper's own
    package directory, and whatever the caller names for the provider shim.
    Anything else under the home directory stays denied — including the tree
    this file lives in.
    """
    candidates = [
        sys.prefix,
        sys.base_prefix,
        helper_package_dir(),
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
        # The keychain is not a file the home denial covers, and this is the
        # hole that denial looked like it closed.
        #
        # `~/Library/Keychains/login.keychain-db` is under $HOME, so a direct
        # read is refused — but nothing reads it directly. `security
        # find-generic-password -a llm/llm_api_key -s openai4s -w` asks
        # *securityd* to read it, and `allow default` left the Mach services
        # that reach securityd wide open. The item is there because
        # `KeychainBackend` put it there through the same `/usr/bin/security`,
        # and the helper deliberately has the network to send it out over.
        # Verified by execution on macOS 26.5: with only the home denial the
        # confined command printed the credential; with these denials it exits
        # 44 and `security list-keychains` fails, while TLS and
        # `ssl.create_default_context()` are unaffected — the trust services
        # (trustd, ocspd) are deliberately not on this list.
        "(deny mach-lookup",
        '    (global-name "com.apple.SecurityServer")',
        '    (global-name "com.apple.securityd")',
        '    (global-name "com.apple.securityd.xpc")',
        '    (global-name "com.apple.security.agent")',
        '    (global-name "com.apple.security.authhost")',
        '    (global-name "com.apple.security.XPCKeychainSandboxCheck")',
        '    (global-name "com.apple.CoreAuthentication.agent")',
        '    (global-name "com.apple.CoreAuthentication.daemon")',
        '    (global-name "com.apple.ctkd.token-client"))',
        # The keychain stores that do *not* live under $HOME, so the home
        # denial says nothing about them. Not `/System/Library/Keychains`,
        # which holds the system root certificates rather than secrets.
        "(deny file-read* file-write*",
        '    (subpath "/Library/Keychains")',
        '    (subpath "/Network/Library/Keychains")',
        '    (subpath "/private/var/db/SystemKey"))',
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
        # A private PID namespace. Without it, `--proc /proc` shows the *host*
        # PID namespace, so the shim could traverse `/proc/<daemon-pid>/root` to
        # reach the daemon's files behind the $HOME tmpfs, or read process
        # metadata, and exfiltrate over its allowed network. In its own namespace
        # /proc shows only the sandbox.
        "--unshare-pid",
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
        # The whole-root read-only bind exposes host control sockets — Docker,
        # Podman (rootful and rootless), the credential/runtime services — under
        # /run, and a read-only mount does not stop `connect(2)`. The shim could
        # otherwise ask a container service to mount the host home and bypass the
        # tmpfs. An empty tmpfs over /run hides every one of them; a compute
        # helper needs nothing under /run (DNS and CA certs live under /etc).
        # Kept after the $HOME tmpfs so the first `--tmpfs` stays the home mask.
        "--tmpfs",
        "/run",
    ]
    for path in list(read_paths) or runtime_read_paths():
        # Bound back *after* the tmpfs so the interpreter can still start; a
        # read-only bind, because nothing here needs to write to them.
        wrapped.extend(["--ro-bind", path, path])
    # Masking /run also hid the DNS resolver config: on systemd-resolved and
    # NetworkManager hosts /etc/resolv.conf symlinks to a file under /run, and
    # the helper's entire job is to reach a provider API. Rebind the resolved
    # target back over the tmpfs so name resolution still works, without
    # re-exposing the control sockets. After the /run tmpfs so it wins.
    wrapped.extend(_runtime_dns_rebinds())
    wrapped.extend(["--bind", stage_dir, stage_dir, "--chdir", stage_dir, "--"])
    wrapped.extend(str(part) for part in argv)
    return wrapped


def _runtime_dns_rebinds() -> list[str]:
    """`--ro-bind-try` args that keep DNS working after `/run` is masked.

    ``/etc/resolv.conf`` commonly symlinks to ``/run/systemd/resolve/…`` (or a
    NetworkManager path under ``/run``), which the ``/run`` tmpfs hides. Rebind
    the resolved target back; a ``-try`` bind is skipped when the path is absent,
    so on a host whose resolv.conf is a plain file outside ``/run`` this is a
    no-op.
    """
    rebinds: list[str] = []
    try:
        resolved = os.path.realpath("/etc/resolv.conf")
    except OSError:  # pragma: no cover - unreadable /etc
        resolved = ""
    if resolved.startswith("/run/"):
        rebinds.extend(["--ro-bind-try", resolved, resolved])
    return rebinds


def network_isolated() -> bool:
    """Whether the boundary also isolates the network. It does not.

    Stated as its own question rather than folded into `available()`, because
    the two are separate capabilities and conflating them is how "the helper is
    confined" comes to be read as "the helper cannot reach the network".
    """
    return False


#: How long a boundary self-test may take. It starts one process and lists one
#: directory; anything slower is a host problem, not a slow probe.
SELF_TEST_TIMEOUT_S = 30.0

#: The macOS probe's verdicts, so a failure names which invariant broke rather
#: than reporting a bare exit code as "no filesystem boundary". Deliberately
#: not 1 or 2: `sandbox-exec` failing to compile or apply the profile exits
#: with small codes of its own, and a verdict must not be confused with the
#: backend never having run.
_PROBE_HOME_READABLE = 81
_PROBE_KEYCHAIN_REACHABLE = 82
_PROBE_FAILURES = {
    _PROBE_HOME_READABLE: (
        "the user's home directory is still readable from inside it"
    ),
    _PROBE_KEYCHAIN_REACHABLE: (
        "the user's keychain is still reachable from inside it (securityd "
        "answered), so a provider shim could read stored credentials and send "
        "them out over the network the helper is allowed"
    ),
}

#: Cached self-test verdicts, keyed by everything that could change the answer.
_SELF_TEST: dict[tuple, tuple[bool, str]] = {}
_SELF_TEST_LOCK = threading.Lock()


def _self_test_key(executable: str) -> tuple:
    """Everything a cached verdict is only valid for.

    The binary and its mtime (an upgrade or a replacement changes the answer),
    the home the boundary is built around, and the platform. A verdict cached
    across a configuration change would be exactly the stale confidence this
    self-test exists to remove.
    """
    try:
        stamp = os.stat(executable).st_mtime_ns
    except OSError:
        stamp = 0
    return (sys.platform, executable, stamp, _canonical(Path.home()))


def _probe_argv(executable: str, stage: str, home: str) -> list[str]:
    """A bounded command that only passes if the boundary really applied.

    Not "did the backend exit 0" — a bwrap that cannot unshare, or a Seatbelt
    profile that failed to compile, can still run a command. The test is the
    filesystem invariant itself, plus — on macOS — the keychain one, which the
    filesystem invariant does not imply: the secret is read *by securityd*, so
    an unreadable keychain file proves nothing on its own.

    On Linux the invariant is *mount identity*, not emptiness. ``build_bwrap_argv``
    binds the interpreter's own runtime paths back over the home tmpfs, and on a
    user install those paths live under ``$HOME`` — so the tmpfs is legitimately
    non-empty, and an emptiness check would report a working boundary as broken,
    which makes ``enforce`` reject BYOC and ``auto`` degrade to unconfined. So it
    compares the home's device id inside the sandbox against the host's real
    one, exactly as the resident helper does: a different device means the tmpfs
    is in place. On macOS the home is denied outright, so the read must fail.
    """
    if sys.platform.startswith("linux"):
        try:
            host_dev = str(os.stat(home).st_dev)
        except OSError:
            host_dev = ""
        # A non-empty inside device that differs from the host's is the tmpfs.
        script = (
            f'd="$(stat -c %d {home!r} 2>/dev/null)"; '
            f'[ -n "$d" ] && [ "$d" != {host_dev!r} ]'
        )
        return build_bwrap_argv(
            ["/bin/sh", "-c", script], stage, executable=executable, home=home
        )
    # Two invariants on macOS, because the home denial only covers one of them.
    # `security list-keychains` succeeding means securityd is reachable, and a
    # reachable securityd will read the login keychain on the helper's behalf
    # however unreadable the file itself is. The check is one-directional on
    # purpose: only *success* fails the probe, so a host where `security` is
    # missing or unrunnable is not reported as unconfined for that reason.
    #
    # Distinct exit codes, because "the boundary did not hold" is not a useful
    # thing to tell someone who then has to work out which half of it.
    script = (
        f"ls {home!r} >/dev/null 2>&1 && exit {_PROBE_HOME_READABLE}; "
        f"/usr/bin/security list-keychains >/dev/null 2>&1 && "
        f"exit {_PROBE_KEYCHAIN_REACHABLE}; "
        "exit 0"
    )
    return [executable, "-p", build_profile(stage, home=home), "/bin/sh", "-c", script]


def self_test(*, force: bool = False) -> tuple[bool, str]:
    """Actually establish a boundary once, and see whether it holds.

    ``shutil.which`` answers whether a binary is installed. On a Linux host
    where unprivileged user namespaces or mounts are disabled, ``bwrap`` is
    installed and cannot confine anything — so confinement was reported active,
    the enforce gate let the op proceed, and the real invocation died before the
    helper started. That failure was then classified as an indeterminate remote
    operation: the worst available reading of a host that simply cannot sandbox.

    The verdict is cached, and the key covers the backend binary, its mtime and
    the home directory — so a configuration or environment change invalidates
    it rather than being papered over by a stale success.
    """
    executable = shutil.which(_BWRAP if sys.platform.startswith("linux") else _SEATBELT)
    if not executable:
        binary = _BWRAP if sys.platform.startswith("linux") else _SEATBELT
        return (
            False,
            f"{binary} is not on PATH, so no OS boundary can be built for the "
            f"provider helper on this host; install it, or accept the "
            f"degradation that OPENAI4S_COMPUTE_CONFINEMENT=auto reports",
        )
    key = _self_test_key(executable)
    if not force:
        with _SELF_TEST_LOCK:
            cached = _SELF_TEST.get(key)
        if cached is not None:
            return cached

    verdict: tuple[bool, str]
    backend = (
        "Linux bubblewrap" if sys.platform.startswith("linux") else "macOS Seatbelt"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="openai4s-confine-selftest-") as stage:
            argv = _probe_argv(executable, stage, _canonical(Path.home()))
            completed = subprocess.run(
                argv, capture_output=True, timeout=SELF_TEST_TIMEOUT_S
            )
        if completed.returncode == 0:
            verdict = (True, backend)
        else:
            # Only the macOS probe assigns meaning to its exit codes; the Linux
            # one is a plain `[ … ]` test whose 1 means nothing more than false.
            named = (
                None
                if sys.platform.startswith("linux")
                else _PROBE_FAILURES.get(completed.returncode)
            )
            if named:
                verdict = (
                    False,
                    f"{backend} ran, but the boundary it established does not "
                    f"hold here: {named}",
                )
            else:
                detail = (
                    completed.stderr.decode("utf-8", "replace").strip()
                    or f"exit {completed.returncode}"
                )
                verdict = (
                    False,
                    f"{backend} is installed but did not establish a filesystem "
                    f"boundary here: {detail}",
                )
    except subprocess.TimeoutExpired:
        verdict = (
            False,
            f"{backend} did not answer its boundary self-test within "
            f"{SELF_TEST_TIMEOUT_S:.0f}s",
        )
    except Exception as e:  # noqa: BLE001
        # Anything at all. This runs on the path of an ordinary compute
        # operation, and a probe that blows up must degrade the *confinement*
        # answer rather than fail the caller's work — "I could not establish a
        # boundary" is exactly what an unrunnable probe means.
        verdict = (False, f"{backend} could not be started: {type(e).__name__}: {e}")

    with _SELF_TEST_LOCK:
        _SELF_TEST[key] = verdict
    return verdict


def reset_self_test_cache() -> None:
    """Forget every cached verdict. For tests and for configuration changes."""
    with _SELF_TEST_LOCK:
        _SELF_TEST.clear()


def available() -> tuple[bool, str]:
    """Whether a boundary can be established here, and why not when it cannot.

    "Installed" is not "works": this runs the self-test above rather than
    reporting on the presence of a binary.
    """
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        return self_test()
    return False, f"no confinement backend for platform {sys.platform!r}"


def posture(mode: str) -> dict:
    """The one description of this host's confinement, for every surface.

    Both the runtime status and ``openai4s doctor`` read this. They disagreed
    before: doctor reported, unconditionally, that no OS boundary existed and
    told the user to weaken ``enforce`` to ``auto`` — on a host where the
    boundary was implemented, self-tested and applied.
    """
    normalised = (mode or "auto").strip().lower()
    if normalised == "off":
        return {
            "mode": "off",
            "enforced": False,
            "state": "disabled",
            "backend": None,
            "network_isolated": False,
            "detail": (
                "the provider helper is spawned with no OS boundary by "
                "explicit configuration"
            ),
        }
    ok, reason = available()
    if ok:
        isolated = network_isolated()
        return {
            "mode": normalised,
            "enforced": True,
            "state": "active",
            "backend": reason,
            # Its own field, and never folded into `enforced`. "The helper is
            # confined" is read by most people as "the helper cannot phone
            # home", and the filesystem boundary says nothing about the
            # network — a boundary nobody mentions is one people assume.
            "network_isolated": isolated,
            "detail": (
                f"the provider helper runs under {reason}: writes confined to "
                f"its stage directory and the user's home replaced, so "
                f"credentials, keys and history are not reachable. The helper "
                f"verifies this from inside before it reads a credential and "
                f"exits without acting if it does not hold. "
                + (
                    "The network is isolated too."
                    if isolated
                    else "The network is NOT isolated: outbound egress is a "
                    "separate capability and it is not enabled, so the helper "
                    "can still reach the internet."
                )
            ),
        }
    return {
        "mode": normalised,
        "enforced": False,
        "state": "unavailable",
        "backend": None,
        "network_isolated": False,
        "detail": (
            f"no OS boundary can be established for the provider helper here: "
            f"{reason}"
        ),
    }


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
    "SELF_TEST_TIMEOUT_S",
    "available",
    "build_bwrap_argv",
    "build_profile",
    "helper_package_dir",
    "network_isolated",
    "posture",
    "probe_environment",
    "reset_self_test_cache",
    "runtime_read_paths",
    "self_test",
    "wrap",
]
