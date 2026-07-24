"""The provider helper's OS boundary, asserted by running it.

The helper was designed to be confined and shipped unconfined: it carries a
self-check (`expect_confined`) and an exit code for failing it, and nothing on
the host ever wrapped it in anything. `confinement_status()` said
`enforced: False` and `enforce` refused every op — honest, and still a
designed-but-not-built boundary.

What makes a claim about a sandbox worth anything is that something inside it
tried the forbidden thing and was stopped. So the tests below execute a real
probe under the real profile rather than inspecting the profile text: a
profile that reads correctly and denies nothing is the failure mode, and only
execution can tell the two apart.

One such failure was found exactly this way. Denying `file-read*` over the home
directory also denies the *metadata* reads `execvp` and dyld perform on the
interpreter, so `sandbox-exec` died with "Operation not permitted" before the
helper started — a profile that looked stricter and confined nothing, because
nothing ran. `file-read-data` is the correct granularity, and the difference is
invisible without running it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

from openai4s.security import byoc_confinement as bc

_PROBE = r"""
import json, os, sys
out = {}


def t(key, fn):
    try:
        out[key] = fn() or "OK"
    except PermissionError:
        out[key] = "DENIED"
    except FileNotFoundError:
        out[key] = "ABSENT"
    except Exception as exc:
        out[key] = "ERR:" + type(exc).__name__


home = os.path.expanduser("~")
t("home_list", lambda: os.listdir(home) and "READABLE")
t("home_file", lambda: open(os.path.join(home, sys.argv[2]), "rb").read() and "READABLE")
t("stage_write", lambda: open(os.path.join(sys.argv[1], "w.txt"), "w").write("x") and "OK")
t("home_write", lambda: open(os.path.join(home, ".o4s-probe"), "w").write("x") and "ALLOWED")
print(json.dumps(out))
"""

macos_only = pytest.mark.skipif(
    sys.platform != "darwin" or not bc.available()[0],
    reason="the confinement backend implemented so far is macOS Seatbelt",
)


@pytest.fixture
def confined_probe(tmp_path, monkeypatch):
    """Run the probe under the real profile, with a fake home holding a secret."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "secret.txt").write_text("an-api-key", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    def run(*, wrapped: bool = True) -> dict:
        with tempfile.TemporaryDirectory() as stage:
            argv = [sys.executable, "-I", "-c", _PROBE, stage, "secret.txt"]
            if wrapped:
                argv = bc.wrap(argv, stage)
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=90)
            assert proc.returncode == 0, proc.stderr
            return json.loads(proc.stdout.strip().splitlines()[-1])

    return run


# --------------------------------------------------------------------------
# the boundary, tried from inside
# --------------------------------------------------------------------------


@macos_only
def test_the_home_directory_is_unreadable_from_inside(confined_probe):
    """The invariant the helper itself probes for before it reads a
    credential. Without it, a compromised provider shim reads every key, token
    and ssh identity the user owns."""
    result = confined_probe()
    assert result["home_list"] == "DENIED"
    assert result["home_file"] == "DENIED"


@macos_only
def test_the_same_probe_succeeds_unconfined(confined_probe):
    """The control. Without it, a profile that fails to apply at all — or a
    probe that was wrong about where it looked — reads as a passing test."""
    result = confined_probe(wrapped=False)
    assert result["home_list"] == "READABLE"
    assert result["home_file"] == "READABLE"


@macos_only
def test_the_stage_directory_is_the_only_place_it_may_write(confined_probe):
    """A helper that cannot write its reply cannot answer at all, and one that
    can write anywhere is not confined."""
    result = confined_probe()
    assert result["stage_write"] == "OK"
    assert result["home_write"] == "DENIED"


@macos_only
def test_the_interpreter_can_still_start(confined_probe):
    """The failure this file was written after: `file-read*` over the home
    directory denies the metadata reads execvp needs, so the exec fails and
    nothing runs — a profile that confines nothing because it confines
    everything."""
    assert confined_probe()["stage_write"] == "OK"


@macos_only
def test_the_stage_path_is_canonicalised():
    """macOS temp directories live under /var/folders, where /var is a symlink
    to /private/var. The kernel resolves before it evaluates the profile, so a
    rule written with the unresolved path matches nothing and the helper cannot
    write its reply."""
    with tempfile.TemporaryDirectory() as stage:
        profile = bc.build_profile(stage)
        assert os.path.realpath(stage) in profile


# --------------------------------------------------------------------------
# the keychain, which the filesystem invariant does not cover
# --------------------------------------------------------------------------


def test_the_profile_denies_the_keychain_services():
    """Codex P1, stated in the profile itself so it is reviewable off-macOS.

    `~/Library/Keychains/login.keychain-db` is under the denied home, but
    nothing reads it directly: `security find-generic-password` asks securityd,
    and `allow default` left every Mach service that reaches securityd open.
    """
    profile = bc.build_profile("/tmp/stage-x", home="/tmp/home-x")
    assert "(deny mach-lookup" in profile
    for service in (
        "com.apple.SecurityServer",
        "com.apple.securityd",
        "com.apple.securityd.xpc",
        "com.apple.security.agent",
    ):
        assert f'(global-name "{service}")' in profile, service
    # The stores outside $HOME the home denial says nothing about...
    assert '(subpath "/Library/Keychains")' in profile
    # ...but not the system roots, which TLS trust validation reads.
    assert '(subpath "/System/Library/Keychains")' not in profile
    # The denial must come after `allow default`, or it never matches.
    assert profile.index("(allow default)") < profile.index("(deny mach-lookup")


@macos_only
def test_the_keychain_cannot_be_read_from_inside(tmp_path):
    """The exfiltration, tried for real against a scratch keychain.

    Unconfined, `security find-generic-password -w` prints the secret — which
    is exactly how `KeychainBackend` stores and retrieves the LLM API key. The
    helper has the network by design, so a provider shim that could still reach
    securityd could read the key and send it out. Never the user's own login
    keychain: this creates, reads and deletes its own file.
    """
    keychain = str(tmp_path / "probe.keychain-db")
    canary = "CANARY-not-a-real-key"

    def security(*args, wrapped: bool = False):
        argv = ["/usr/bin/security", *args]
        if wrapped:
            with tempfile.TemporaryDirectory() as stage:
                argv = bc.wrap(argv, stage)
                return subprocess.run(argv, capture_output=True, text=True, timeout=90)
        return subprocess.run(argv, capture_output=True, text=True, timeout=90)

    assert security("create-keychain", "-p", "probe-pw", keychain).returncode == 0
    try:
        assert security("unlock-keychain", "-p", "probe-pw", keychain).returncode == 0
        assert (
            security(
                "add-generic-password",
                "-a",
                "llm/llm_api_key",
                "-s",
                "openai4s",
                "-w",
                canary,
                keychain,
            ).returncode
            == 0
        )
        find = (
            "find-generic-password",
            "-a",
            "llm/llm_api_key",
            "-s",
            "openai4s",
            "-w",
            keychain,
        )

        # The control: without the boundary the secret comes straight back.
        plain = security(*find)
        assert plain.returncode == 0 and canary in plain.stdout

        confined = security(*find, wrapped=True)
        assert confined.returncode != 0, "the confined helper read the credential"
        assert canary not in confined.stdout
        assert canary not in confined.stderr
    finally:
        security("delete-keychain", keychain)


@macos_only
def test_the_self_test_probes_the_keychain_too():
    """A boundary the self-test does not check is one that can regress in
    silence — and `available()` is what `enforce` gates on."""
    argv = bc._probe_argv("sandbox-exec", "/tmp/stage-x", "/tmp/home-x")
    script = argv[-1]
    assert "list-keychains" in script
    # One-directional: only a *successful* keychain read fails the probe, so a
    # host without `security` is not misreported as unconfined.
    assert f"&& exit {bc._PROBE_KEYCHAIN_REACHABLE}" in script
    # ...and the verdict codes must not collide with the small exit codes
    # `sandbox-exec` uses when it fails to apply the profile at all, or "the
    # backend never ran" would be reported as "the boundary does not hold".
    assert bc._PROBE_KEYCHAIN_REACHABLE > 2 and bc._PROBE_HOME_READABLE > 2
    assert "keychain" in bc._PROBE_FAILURES[bc._PROBE_KEYCHAIN_REACHABLE]
    ok, reason = bc.self_test(force=True)
    assert ok, f"the hardened profile no longer self-tests: {reason}"


# --------------------------------------------------------------------------
# what the host says about it
# --------------------------------------------------------------------------


def test_an_unavailable_backend_says_why():
    """`unavailable` with no reason is what makes a gap look like an
    oversight. Linux is a stated open decision, not an omission."""
    ok, reason = bc.available()
    assert isinstance(ok, bool)
    assert reason, "availability must always carry its reason"
    if not ok:
        assert len(reason) > 20


def test_wrapping_refuses_rather_than_returning_a_bare_argv(monkeypatch):
    """A wrap that silently returned the unwrapped command would be the worst
    possible failure: every caller would believe it was confined."""
    monkeypatch.setattr(bc, "available", lambda: (False, "no backend here"))
    with pytest.raises(bc.ConfinementUnavailable, match="no backend here"):
        bc.wrap([sys.executable, "-c", "pass"], "/tmp")


def test_the_manager_reports_the_boundary_it_actually_applies(tmp_path):
    """`confinement_status()` is what the UI and the release gate read."""
    import types

    from openai4s.compute.manager import ComputeManager

    (tmp_path / "skills").mkdir()
    cfg = types.SimpleNamespace(data_dir=tmp_path, skills_dir=tmp_path / "skills")
    status = ComputeManager(cfg).confinement_status()

    available, _reason = bc.available()
    assert status["enforced"] is available
    assert status["state"] == ("active" if available else "unavailable")
    assert status["detail"]


def test_confinement_off_is_reported_as_a_deliberate_choice(tmp_path, monkeypatch):
    import types

    from openai4s.compute.manager import ComputeManager

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "off")
    (tmp_path / "skills").mkdir()
    cfg = types.SimpleNamespace(data_dir=tmp_path, skills_dir=tmp_path / "skills")
    status = ComputeManager(cfg).confinement_status()

    assert status["state"] == "disabled"
    assert status["enforced"] is False
    assert "explicit configuration" in status["detail"]


# --------------------------------------------------------------------------
# Linux: the same filesystem invariant, and an honest answer about the network
# --------------------------------------------------------------------------


def test_the_linux_wrapper_replaces_the_home_directory():
    """The invariant, by owner decision, is the filesystem one on both
    platforms. `--tmpfs $HOME` is what makes the user's files not be there."""
    argv = bc.build_bwrap_argv(
        ["python", "-c", "pass"],
        "/tmp/stage",
        executable="/usr/bin/bwrap",
        home="/home/researcher",
        read_paths=("/opt/py",),
    )
    assert "--tmpfs" in argv
    # Canonicalised, like every other path in the boundary: the kernel
    # resolves before it evaluates the mount, so an unresolved path is a
    # rule against a directory that does not exist.
    assert argv[argv.index("--tmpfs") + 1] == os.path.realpath("/home/researcher")


def test_the_linux_wrapper_binds_the_runtime_back_over_the_tmpfs():
    """bwrap applies mounts in order, so the interpreter's own paths have to
    come *after* the tmpfs or nothing can start — the Linux form of the same
    failure the macOS profile hit."""
    argv = bc.build_bwrap_argv(
        ["python"],
        "/tmp/stage",
        executable="/usr/bin/bwrap",
        home="/home/researcher",
        read_paths=("/opt/py",),
    )
    assert argv.index("--tmpfs") < argv.index("--ro-bind", argv.index("--tmpfs"))
    assert "/opt/py" in argv


def test_the_linux_wrapper_makes_only_the_stage_writable():
    argv = bc.build_bwrap_argv(
        ["python"],
        "/tmp/stage",
        executable="/usr/bin/bwrap",
        home="/home/researcher",
        read_paths=("/opt/py",),
    )
    binds = [argv[i + 1] for i, part in enumerate(argv) if part == "--bind"]
    assert binds == [os.path.realpath("/tmp/stage")]


def test_the_linux_wrapper_isolates_the_pid_namespace():
    """Codex P1: `--proc /proc` without a private PID namespace shows the host's
    namespace, so the shim could traverse /proc/<daemon-pid>/root to reach the
    daemon's files behind the $HOME tmpfs."""
    argv = bc.build_bwrap_argv(
        ["python"], "/tmp/stage", executable="/usr/bin/bwrap", home="/home/r"
    )
    assert "--unshare-pid" in argv, "the helper shares the host PID namespace"
    # It must precede the --proc mount so that /proc reflects the new namespace.
    assert argv.index("--unshare-pid") < argv.index("--proc")


def test_the_linux_wrapper_masks_host_control_sockets():
    """Codex P1: the whole-root ro-bind exposes Docker/Podman/credential sockets
    under /run, and a read-only mount does not stop connect(2). An empty tmpfs
    over /run hides them — placed after the root bind so it wins, and after the
    $HOME tmpfs so the home mask stays first."""
    argv = bc.build_bwrap_argv(
        ["python"],
        "/tmp/stage",
        executable="/usr/bin/bwrap",
        home="/home/researcher",
        read_paths=("/opt/py",),
    )
    tmpfs_targets = [argv[i + 1] for i, part in enumerate(argv) if part == "--tmpfs"]
    assert "/run" in tmpfs_targets, "host runtime sockets under /run are exposed"
    # The /run tmpfs overlays the whole-root bind that would otherwise expose it.
    run_at = argv.index("/run", argv.index("--tmpfs"))
    assert argv.index("/", argv.index("--ro-bind")) < run_at
    # ...and the home mask is still the first tmpfs (a contract other tests rely
    # on), so masking /run did not displace it.
    assert argv[argv.index("--tmpfs") + 1] == os.path.realpath("/home/researcher")


def test_the_linux_wrapper_rebinds_dns_config_hidden_by_the_run_tmpfs(monkeypatch):
    """The /run mask (which hides the control sockets) also hides the DNS
    resolver config, since /etc/resolv.conf symlinks under /run on
    systemd-resolved hosts — and the helper's whole job is reaching a provider
    API. The resolved target must be rebound back over the tmpfs."""
    resolv = "/run/systemd/resolve/stub-resolv.conf"
    real_realpath = os.path.realpath

    def fake_realpath(p, **kw):
        # os.path.realpath is shared (pathlib.resolve calls it with strict=), so
        # forward everything but the one path this test cares about.
        return resolv if str(p) == "/etc/resolv.conf" else real_realpath(p, **kw)

    monkeypatch.setattr(bc.os.path, "realpath", fake_realpath)
    argv = bc.build_bwrap_argv(
        ["python"], "/tmp/stage", executable="/usr/bin/bwrap", home="/home/r"
    )
    assert "--ro-bind-try" in argv
    assert resolv in argv
    # It is rebound *after* the /run tmpfs so it survives the mask.
    assert argv.index("/run", argv.index("--tmpfs")) < argv.index(resolv)


def test_the_linux_wrapper_does_not_claim_network_isolation():
    """Network isolation is a separate capability and it is not enabled.
    Adding `--unshare-net` here would cut the helper off from the API that is
    its entire purpose — and claiming it without adding it would be worse."""
    argv = bc.build_bwrap_argv(
        ["python"], "/tmp/stage", executable="/usr/bin/bwrap", home="/home/r"
    )
    assert "--unshare-net" not in argv
    assert bc.network_isolated() is False


def test_the_status_says_out_loud_that_the_network_is_not_isolated(
    tmp_path, monkeypatch
):
    """ "The helper is confined" is read as "the helper cannot phone home"."""
    import types

    from openai4s.compute.manager import ComputeManager

    monkeypatch.setattr(bc, "available", lambda: (True, "Linux bubblewrap"))
    (tmp_path / "skills").mkdir()
    cfg = types.SimpleNamespace(data_dir=tmp_path, skills_dir=tmp_path / "skills")
    status = ComputeManager(cfg).confinement_status()

    assert status["enforced"] is True
    assert status["network_isolated"] is False
    assert "NOT isolated" in status["detail"]


def test_the_host_supplies_the_anchor_the_probe_needs():
    """A confined process cannot obtain the device id of the real home, which
    is exactly why the comparison value has to be handed in from outside."""
    env = bc.probe_environment()
    assert env["OPENAI4S_HOST_HOME_DEV"].isdigit()


def test_the_helper_probe_reads_that_anchor(monkeypatch, tmp_path):
    """Drive the helper's own check rather than restating it: a host that
    supplies an anchor the helper ignores is confinement theatre."""
    import openai4s_compute_provider._resident as resident

    if sys.platform == "darwin":
        pytest.skip("the anchor path is the Linux branch of the probe")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI4S_HOST_HOME_DEV", str(os.stat(tmp_path).st_dev))
    probe = resident.Resident.__dict__["_probe_confined"]
    assert probe(object()) is False, "same device: not confined"

    monkeypatch.setenv("OPENAI4S_HOST_HOME_DEV", "999999")
    assert probe(object()) is True, "a different device means the tmpfs is in place"
