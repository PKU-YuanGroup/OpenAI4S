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
    assert "env" not in seen[0][1]


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
