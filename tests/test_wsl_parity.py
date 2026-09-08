"""Offline regression contracts for Windows packaging and WSL adapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.security import windows_dpapi, wsl
from openai4s.security.sandbox import wrap_bwrap_command
from tests.test_release_gates import (
    _load_script,
    _run_windows_bootstrap_install,
    _write_fake_linux_payload,
)


@pytest.mark.stubbed_backend
def test_wsl_mount_aliases_and_linux_tool_order(monkeypatch):
    data = (
        "11 1 0:9 / /mnt/c rw - 9p C: rw,aname=drvfs;path=C:\\134\n"
        "12 1 0:9 /Users /windows\\040files rw - 9p C: rw,aname=drvfs;path=C:\\134\n"
        "13 1 8:1 / / rw - ext4 /dev/sda rw\n"
    )
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: data)
    assert wsl.windows_mounts() == ("/windows files", "/mnt/c")
    assert (
        wsl.linux_path("/env/bin:/mnt/c/tools:/usr/bin:/env/bin:/windows files/bin:.")
        == "/env/bin:/usr/bin"
    )


@pytest.mark.stubbed_backend
def test_wsl_masks_host_roots_before_restoring_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(wsl, "is_wsl", lambda: True)
    monkeypatch.setattr(wsl, "read_denials", lambda: (("subpath", str(tmp_path)),))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = wrap_bwrap_command(
        ["python3"],
        executable="bwrap",
        workspace=workspace,
        temp_dir=workspace,
        seccomp_fd=42,
    )
    assert "--unshare-pid" in command
    assert command[command.index("--seccomp") + 1] == "42"
    assert command.index("--tmpfs") < command.index("--bind")
    assert f"/proc/{os.getpid()}/environ" not in command


@pytest.mark.stubbed_backend
def test_dpapi_passes_secret_only_on_stdin_and_namespaces_the_key(monkeypatch):
    seen = []
    monkeypatch.setattr(
        windows_dpapi, "powershell_path", lambda: "/windows/powershell.exe"
    )
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "machine-one")
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"value":null}')

    monkeypatch.setattr(subprocess, "run", run)
    canary = "synthetic\nUnicode \u79d1\u5b66; $value"
    windows_dpapi.request("put", "first-store", "model", "key", canary)
    windows_dpapi.request("put", "second-store", "model", "key", canary)
    assert canary not in str(seen[0][0])
    first = json.loads(seen[0][1]["input"])
    assert first["value"] == canary
    assert first["key"] != json.loads(seen[1][1]["input"])["key"]
    # The interop child crosses into Windows, and WSLENV is the only thing that
    # carries a Linux variable across. It must export nothing: the daemon's own
    # environment holds the credentials this module exists to protect.
    assert seen[0][1]["env"]["WSLENV"] == ""
    assert canary not in "\n".join(f"{k}={v}" for k, v in seen[0][1]["env"].items())
    assert seen[0][1]["start_new_session"] is True


@pytest.mark.stubbed_backend
def test_dpapi_failure_does_not_echo_backend_output(monkeypatch):
    monkeypatch.setattr(
        windows_dpapi, "powershell_path", lambda: "/windows/powershell.exe"
    )
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "machine-one")
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1,
            stdout=b"synthetic-sensitive-value",
            stderr=b"synthetic-sensitive-value",
        ),
    )
    with pytest.raises(RuntimeError, match="^Windows secure storage operation failed$"):
        windows_dpapi.request("get", "store", "model", "key")


def test_bundle_console_script_survives_relocation_and_preserves_arguments(tmp_path):
    if os.name != "posix":
        pytest.skip("console scripts target Linux/macOS")
    module = _load_script("relocate_bundle_scripts")
    runtime = tmp_path / "build runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python3").symlink_to(sys.executable)
    command = runtime / "bin/science"
    command.write_text("#!/gone/build/python3\nimport sys\nprint(repr(sys.argv[1:]))\n")
    command.chmod(0o755)
    assert module.relocate(runtime) == 1
    relocated = tmp_path / "installed \u79d1\u5b66"
    runtime.rename(relocated)
    result = subprocess.run(
        [str(relocated / "bin/science"), "two words", "\u6570\u636e", "$literal"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == repr(["two words", "\u6570\u636e", "$literal"])


@pytest.mark.parametrize("builder", ["build_linux_bundle.sh", "build_macos_dmg.sh"])
def test_bundled_cli_loads_its_package_and_preserves_the_working_directory(
    tmp_path, builder
):
    if os.name != "posix" or sys.version_info < (3, 11):
        pytest.skip("bundled runtimes are POSIX Python 3.11+")
    source = (Path(__file__).parents[1] / "scripts" / builder).read_text()
    template = re.search(r"<<'CLI'\n(.*?)\nCLI\n", source, re.S)
    assert template is not None
    app = tmp_path / "application"
    (app / "runtime/bin").mkdir(parents=True)
    (app / "runtime/bin/python3").symlink_to(sys.executable)
    package = app / "src/openai4s"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(
        "import os\nprint('packaged')\nprint(os.getcwd())\n"
    )
    cli = app / ("bin/openai4s" if "linux" in builder else "runtime/bin/openai4s")
    cli.parent.mkdir(exist_ok=True)
    cli.write_text(template[1] + "\n")
    cli.chmod(0o755)
    workspace = tmp_path / "scientist project"
    workspace.mkdir()
    (workspace / "openai4s.py").write_text(
        "raise RuntimeError('cwd shadow imported')\n"
    )
    result = subprocess.run(
        [str(cli)], cwd=workspace, text=True, capture_output=True, check=True
    )
    assert result.stdout.splitlines() == ["packaged", str(workspace)]


def test_default_web_page_rejects_a_missing_hashed_asset(tmp_path):
    contract = _load_script("bundle_contract")
    web = tmp_path / "openai4s/server/webui"
    (web / "dist").mkdir(parents=True)
    (web / "dist/index.html").write_text(
        '<script src="/static/dist/assets/app-abc.js"></script>'
    )
    with pytest.raises(contract.BundleCheckError, match="app-abc.js"):
        contract.check_web_assets(tmp_path)
    (web / "dist/assets").mkdir()
    (web / "dist/assets/app-abc.js").write_text("// fixture")
    contract.check_web_assets(tmp_path)


def test_a_damaged_same_digest_tree_is_repaired_rather_than_refused(tmp_path):
    """Every payload lives at its own digest, so a tree already there is the
    same bytes -- incomplete, or damaged since. Refusing it made the only
    repair (reinstalling) impossible: the fast path rejects the tree, the
    re-extract lands, and every later launch refuses again."""
    if not shutil.which("flock"):
        pytest.skip("WSL bootstrap requires Linux flock")
    archive = tmp_path / "payload.tar.gz"
    bundle = _write_fake_linux_payload(archive, "9.9.9", "x86_64")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (
        _run_windows_bootstrap_install(tmp_path, archive, digest, bundle).returncode
        == 0
    )

    installed = (tmp_path / "data/app/current").resolve()
    (installed / ".installed").unlink()
    (installed / "bin/openai4s").chmod(0o644)

    repaired = _run_windows_bootstrap_install(tmp_path, archive, digest, bundle)
    assert repaired.returncode == 0, repaired.stderr
    assert (tmp_path / "data/app/current").resolve() == installed
    assert os.access(installed / "bin/openai4s", os.X_OK)


def test_superseded_payloads_are_reclaimed_but_a_running_one_is_not(tmp_path):
    """Content-addressed trees are never overwritten, so without pruning each
    update leaked a full bundle into a WSL VHDX that never shrinks."""
    if not shutil.which("flock"):
        pytest.skip("WSL bootstrap requires Linux flock")
    first = tmp_path / "first.tar.gz"
    bundle = _write_fake_linux_payload(first, "9.9.9", "x86_64")
    assert (
        _run_windows_bootstrap_install(
            tmp_path, first, hashlib.sha256(first.read_bytes()).hexdigest(), bundle
        ).returncode
        == 0
    )
    superseded = (tmp_path / "data/app/current").resolve()

    second = tmp_path / "second.tar.gz"
    _write_fake_linux_payload(second, "9.9.9", "x86_64", marker="rebuilt")
    assert (
        _run_windows_bootstrap_install(
            tmp_path, second, hashlib.sha256(second.read_bytes()).hexdigest(), bundle
        ).returncode
        == 0
    )
    current = (tmp_path / "data/app/current").resolve()
    assert current != superseded
    assert not superseded.exists()
    assert len(list((tmp_path / "data/app").glob("OpenAI4S-*"))) == 1


def test_a_management_command_says_nothing_is_installed_rather_than_guessing(tmp_path):
    """Exit 11 is what lets the Windows launcher install the payload instead of
    refusing `doctor` and `--help` on a first-ever run."""
    if shutil.which("sh") is None:
        pytest.skip("the bootstrap contract needs a POSIX sh")
    script = Path(__file__).parents[1] / "scripts/windows/bootstrap.sh"
    env = {key: value for key, value in os.environ.items()}
    env.update(
        {"HOME": str(tmp_path / "home"), "OPENAI4S_DATA_DIR": str(tmp_path / "empty")}
    )
    result = subprocess.run(
        ["sh", str(script), "cli", "current", "status"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 11, result
    assert "not installed" in result.stderr


def test_bad_same_version_payload_preserves_existing_install(tmp_path):
    if not shutil.which("flock"):
        pytest.skip("WSL bootstrap requires Linux flock")
    archive = tmp_path / "payload.tar.gz"
    bundle = _write_fake_linux_payload(archive, "9.9.9", "x86_64")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = _run_windows_bootstrap_install(tmp_path, archive, digest, bundle)
    assert result.returncode == 0, result.stderr
    pointer = tmp_path / "data/app/current"
    original = pointer.resolve()
    archive.write_bytes(b"synthetic corrupt archive")
    result = _run_windows_bootstrap_install(
        tmp_path, archive, hashlib.sha256(archive.read_bytes()).hexdigest(), bundle
    )
    assert result.returncode != 0
    assert pointer.resolve() == original
    assert (original / "bin/openai4s").is_file()
    assert not list(pointer.parent.glob(".staging-*"))


@pytest.mark.stubbed_backend
def test_a_container_on_a_wsl_kernel_is_not_treated_as_a_wsl_host(monkeypatch):
    """A Docker/Podman container inherits the host's `-microsoft-standard-WSL2`
    kernel release but none of WSL's own surfaces, and must not inherit a
    boundary policy that masks /run and requires a private PID namespace."""
    monkeypatch.setattr(wsl.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        wsl.platform, "release", lambda: "5.15.167.4-microsoft-standard-WSL2"
    )
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(wsl.os.path, "exists", lambda path: False)
    assert wsl.is_wsl() is False
    monkeypatch.setattr(wsl.os.path, "exists", lambda path: path == "/run/WSL")
    assert wsl.is_wsl() is True


@pytest.mark.stubbed_backend
def test_the_wsl_masks_keep_a_resolver_and_a_writable_temp(monkeypatch, tmp_path):
    """`/etc/resolv.conf` points into `/run` or `/mnt/wsl` on WSL2, and an
    empty tmpfs over either leaves it dangling; `/tmp` must stay writable
    because a private tmpfs is already invisible to the host."""
    resolver = tmp_path / "run/systemd/resolve/stub-resolv.conf"
    resolver.parent.mkdir(parents=True)
    resolver.write_text("nameserver 192.0.2.1\n")
    denials = (("subpath", str(tmp_path / "run")), ("subpath", "/tmp"))
    monkeypatch.setattr(wsl, "is_wsl", lambda: True)
    monkeypatch.setattr(wsl, "read_denials", lambda: denials)
    monkeypatch.setattr(wsl.os.path, "realpath", lambda _: str(resolver))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = wrap_bwrap_command(
        ["python3"], executable="bwrap", workspace=workspace, temp_dir=workspace
    )
    assert command[command.index("--ro-bind-try") + 1] == str(resolver)
    assert command.index("--ro-bind-try") < command.index("--remount-ro")
    assert "/tmp" not in command[command.index("--remount-ro") :]


def test_the_hyperv_filter_also_closes_the_io_uring_door(monkeypatch):
    """seccomp classifies syscalls, not ring submissions, so a filter that only
    inspects `socket(2)` is bypassed by IORING_OP_SOCKET on a WSL2 kernel."""
    monkeypatch.setattr(wsl.platform, "machine", lambda: "x86_64")
    arch, socket_nr = wsl._SECCOMP_ARCH["x86_64"]
    program = _decode_bpf(wsl.socket_filter())
    run = lambda nr, family: _run_bpf(program, arch, nr, family)  # noqa: E731
    assert run(wsl.IO_URING_SETUP_NR, 0) == "EPERM"
    assert run(socket_nr, 40) == "EPERM"  # AF_VSOCK
    assert run(socket_nr, 2) == "ALLOW"  # AF_INET
    assert run(1, 0) == "ALLOW"  # write(2)
    assert run(0x40000000 | socket_nr, 40) == "ENOSYS"  # x32
    assert _run_bpf(program, 0x40000003, 102, 0) == "KILL"  # i386 socketcall


def _decode_bpf(blob: bytes) -> list[tuple[int, int, int, int]]:
    import struct

    assert len(blob) % 8 == 0, "sock_filter is exactly 8 bytes"
    return [
        struct.unpack("=HBBI", blob[index : index + 8])
        for index in range(0, len(blob), 8)
    ]


def _run_bpf(program, arch: int, nr: int, family: int) -> str:
    """Interpret the classic-BPF program the way the kernel would."""
    verdicts = {
        0x80000000: "KILL",
        0x00050026: "ENOSYS",
        0x00050001: "EPERM",
        0x7FFF0000: "ALLOW",
    }
    # seccomp_data: nr@0, arch@4, instruction_pointer@8, args[0]@16.
    data = {0: nr, 4: arch, 16: family}
    counter = accumulator = 0
    for _ in range(len(program) + 1):
        code, jump_true, jump_false, k = program[counter]
        if code == 0x20:  # BPF_LD | BPF_W | BPF_ABS
            accumulator = data.get(k, 0)
            counter += 1
        elif code == 0x15:  # BPF_JMP | BPF_JEQ | BPF_K
            counter += 1 + (jump_true if accumulator == k else jump_false)
        elif code == 0x35:  # BPF_JMP | BPF_JGE | BPF_K
            counter += 1 + (jump_true if accumulator >= k else jump_false)
        else:  # BPF_RET | BPF_K
            return verdicts[k]
    raise AssertionError("the filter did not return")
