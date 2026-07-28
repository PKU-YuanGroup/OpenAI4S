"""Which credentials an enforced kernel sandbox actually keeps from a cell.

Two gaps, both measured against a real enforced sandbox on macOS rather than
inferred from the profile text.

**The daemon's access token.** The deny list carried
`("prefix", data_dir / "openai4s.db")`, and the token is a *sibling* of the
database, not a prefix of it — so the DB was blocked and the token was read.
That token gates the whole HTTP API, so a cell holding it can drive every route
the daemon serves, including the ones that execute code.

**The macOS keychain.** `OPENAI4S_SECRET_STORE` defaults to the keychain on
macOS, so the LLM API key lives there, and a cell could run `/usr/bin/security`
and reach it — `security list-keychains` returned the user's keychain path from
inside the sandbox.

Denying the keychain *files* alone does not close that: `securityd` is a
separate daemon that opens them on the caller's behalf, so file rules never
apply to it. The `mach-lookup` denies are what work. The file denies stay too,
because a readable keychain database can be attacked offline.

The obvious worry was TLS — on macOS the Security framework validates
certificates, so cutting off securityd might break every HTTPS fetch a science
cell makes. Measured under exactly these rules: `security list-keychains`
fails while `curl` and `urllib` both return 200. And inside the kernel, HTTPS
fails identically with and without these rules, because the sandbox's own
`(deny network*)` is what stops it — a different policy, deliberately.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from openai4s.security import sandbox


def _profile(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    return sandbox.build_seatbelt_profile(
        str(tmp_path / "ws"),
        str(tmp_path / "tmp"),
        deny_read=sandbox._default_secret_read_denials(str(tmp_path / "ws")),
        allow_raw_network=False,
    )


def test_the_access_token_is_denied_by_name_not_by_luck(tmp_path, monkeypatch):
    """The defect. A prefix rule on `openai4s.db` does not cover a sibling."""
    entries = dict((path, kind) for kind, path in _deny_entries(monkeypatch, tmp_path))
    token = str((tmp_path / "data" / "access-token").resolve())
    assert token in entries, "the daemon's access token is not in the deny list"


def test_the_database_is_still_denied(tmp_path, monkeypatch):
    """Adding an entry must not have displaced the one that was there."""
    paths = [path for _kind, path in _deny_entries(monkeypatch, tmp_path)]
    assert any(path.endswith("openai4s.db") for path in paths)


def test_share_credentials_are_denied(tmp_path, monkeypatch):
    """A share's tokens are what make a read-only snapshot reachable from off
    this machine, so they belong beside the access token rather than one
    directory away from it."""
    paths = [path for _kind, path in _deny_entries(monkeypatch, tmp_path)]
    assert any(path.rstrip("/").endswith("shares") for path in paths)


def _deny_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    return sandbox._default_secret_read_denials(str(tmp_path / "ws"))


# --------------------------------------------------------------------------
# the keychain
# --------------------------------------------------------------------------


def test_the_profile_cuts_off_securityd_not_just_the_keychain_files(
    tmp_path, monkeypatch
):
    """The file rules are not what closes this. `securityd` opens the keychain
    on the caller's behalf, so only refusing to reach it works."""
    profile = _profile(tmp_path, monkeypatch)
    assert 'deny mach-lookup (global-name "com.apple.SecurityServer")' in profile
    assert 'deny mach-lookup (global-name "com.apple.securityd.xpc")' in profile
    # ...and the database itself stays unreadable, for an offline attacker.
    assert "/Library/Keychains" in profile


def test_the_denies_come_after_allow_default(tmp_path, monkeypatch):
    """SBPL is last-match-wins. A deny placed before `(allow default)` is not a
    deny at all, and the profile would still load — silently permissive."""
    profile = _profile(tmp_path, monkeypatch)
    lines = profile.splitlines()
    allow_default = lines.index("(allow default)")
    for needle in ("com.apple.SecurityServer", "/Library/Keychains"):
        placed = [i for i, line in enumerate(lines) if needle in line]
        assert placed, f"{needle} is not in the profile"
        assert min(placed) > allow_default, f"{needle} is overridden by allow default"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_a_sandboxed_process_really_cannot_reach_the_keychain(tmp_path, monkeypatch):
    """Against the real kernel, not the profile text.

    A profile that reads correctly and does not take effect is the failure this
    is here to catch — `auto` degrading silently is a documented concern in
    this codebase, so the assertion has to be about behaviour.
    """
    import subprocess

    profile = _profile(tmp_path, monkeypatch)
    written = tmp_path / "profile.sb"
    written.write_text(profile, encoding="utf-8")
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)

    result = subprocess.run(
        ["sandbox-exec", "-f", str(written), "/usr/bin/security", "list-keychains"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        result.returncode != 0 or not result.stdout.strip()
    ), f"the keychain was reachable from inside the sandbox: {result.stdout[:200]}"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_certificate_validation_still_works_under_the_same_rules(tmp_path, monkeypatch):
    """The reason this fix could have been the wrong trade. On macOS the
    Security framework validates certificates, so cutting off securityd might
    have broken every HTTPS fetch a science cell makes. It does not — but the
    claim is worth a test rather than a comment, because the day it stops being
    true this is how anyone finds out.
    """
    import subprocess

    profile = _profile(tmp_path, monkeypatch)
    # Raw network is denied in the kernel profile by design; this test is about
    # the keychain rules specifically, so it allows network and changes nothing
    # else.
    profile = profile.replace("(deny network*)\n", "")
    written = tmp_path / "net-profile.sb"
    written.write_text(profile, encoding="utf-8")
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)

    result = subprocess.run(
        [
            "sandbox-exec",
            "-f",
            str(written),
            sys.executable,
            "-c",
            "import urllib.request;"
            "print(urllib.request.urlopen('https://example.com', timeout=20).status)",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0 and "urlopen error" in result.stderr:
        pytest.skip(f"no outbound network here: {result.stderr.strip()[:120]}")
    assert result.stdout.strip() == "200", (
        f"TLS broke under the keychain denies: rc={result.returncode} "
        f"stderr={result.stderr[:200]}"
    )


# --------------------------------------------------------------------------
# the Linux branch, forced
# --------------------------------------------------------------------------


def _bwrap(monkeypatch, tmp_path, *, fake_proc=True):
    """Build the bwrap argv with the Linux branch forced.

    Development here is macOS, so `/proc` does not exist and the branch under
    test is never taken by accident. Forcing it is the only way this is checked
    at all before a Linux run — the divergence CLAUDE.md warns about.
    """
    if fake_proc:
        real_exists = os.path.exists
        monkeypatch.setattr(
            sandbox.os.path,
            "exists",
            lambda p: True if str(p).startswith("/proc/") else real_exists(p),
        )
    return sandbox.wrap_bwrap_command(
        ["python3", "-c", "1"],
        executable="/usr/bin/bwrap",
        workspace=str(tmp_path / "ws"),
        temp_dir=str(tmp_path / "tmp"),
        allow_raw_network=False,
        deny_read=(),
    )


def test_the_daemon_environ_is_masked_on_linux(tmp_path, monkeypatch):
    """The part of the PID-namespace gap that carries the credentials.

    The sandbox keeps the host PID namespace on purpose — `Kernel.interrupt()`
    targets `Popen.pid` exactly — so `/proc` still shows the daemon, and the
    daemon's environment is where the API keys are, since the child's own
    environment is allowlisted clean.
    """
    argv = _bwrap(monkeypatch, tmp_path)
    environ = f"/proc/{os.getpid()}/environ"
    assert environ in argv, "the daemon's environment is readable from a cell"
    assert argv[argv.index(environ) - 1] == "/dev/null"
    assert argv[argv.index(environ) - 2] == "--ro-bind"


def test_the_mask_comes_after_the_proc_mount(tmp_path, monkeypatch):
    """Order is the whole thing. `--proc /proc` mounts a fresh procfs, so a
    bind placed before it is replaced and the mask silently does nothing."""
    argv = _bwrap(monkeypatch, tmp_path)
    environ = f"/proc/{os.getpid()}/environ"
    assert argv.index("--proc") < argv.index(environ)


def test_the_mask_is_before_the_command_separator(tmp_path, monkeypatch):
    """Everything after `--` is the command, not bwrap's own arguments."""
    argv = _bwrap(monkeypatch, tmp_path)
    environ = f"/proc/{os.getpid()}/environ"
    assert argv.index(environ) < argv.index("--")


def test_nothing_is_emitted_where_there_is_no_proc(tmp_path, monkeypatch):
    """A bind to a path that does not exist makes bwrap refuse to start, which
    would take the kernel down on any platform without /proc."""
    argv = _bwrap(monkeypatch, tmp_path, fake_proc=False)
    if not Path("/proc").exists():
        assert not any("/environ" in str(arg) for arg in argv)


def test_the_pid_namespace_stays_shared_on_purpose(tmp_path, monkeypatch):
    """Recorded as a decision rather than left implicit. `--unshare-pid` makes
    bwrap interpose an init/reaper, and `Kernel.interrupt()` targets
    `Popen.pid` exactly — so closing the rest of this gap changes the interrupt
    contract and needs a Linux-verified change. What is left open is that a
    cell can see other processes exist; what is closed is the one file that
    carries credentials.
    """
    argv = _bwrap(monkeypatch, tmp_path)
    assert "--unshare-pid" not in argv
    assert "--unshare-ipc" in argv and "--unshare-uts" in argv
