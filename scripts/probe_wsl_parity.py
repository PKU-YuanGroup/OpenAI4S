#!/usr/bin/env python3
"""Collect WSL parity evidence with synthetic data in temporary directories.

Run inside WSL2, from a checkout, as a normal user. ``--windows-scratch`` is
the Linux path to a writable Windows directory, for example /mnt/c/.../temp.
The probe creates its own subdirectory there, tests only its own marker, and
removes it. No real credentials, provider requests, or machine settings are
used. The secret broker may perform its normal temporary canary round trip.

This is a diagnostic, not a passing release gate: exit zero means the report
was collected. Inspect each observation, especially windows_marker_changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(argv: list[str], **kwargs) -> dict:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120, **kwargs)
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def probe(scratch: Path, windows_scratch: Path) -> dict:
    from openai4s.host.bash import BashAuthorizationService
    from openai4s.kernel import Kernel
    from openai4s.sdk.host import build_host, decode_args
    from openai4s.security.secret_broker import SecretBroker

    workspace = scratch / "workspace"
    workspace.mkdir()
    os.chdir(workspace)
    os.environ["OPENAI4S_WORKSPACE"] = str(workspace)
    os.environ["OPENAI4S_DATA_DIR"] = str(scratch / "data")
    os.environ["OPENAI4S_KERNEL_SANDBOX"] = "enforce"
    os.environ.pop("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK", None)
    report = {
        "commit": run(["git", "rev-parse", "HEAD"], cwd=ROOT)["stdout"].strip(),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "uid": os.getuid(),
        "dependencies": {
            name: shutil.which(name)
            for name in (
                "bash",
                "bwrap",
                "secret-tool",
                "python",
                "python3",
                "pip",
                "Rscript",
                "micromamba",
                "conda",
                "node",
                "npx",
                "uv",
                "uvx",
            )
        },
    }
    bootstrap = ROOT / "scripts/windows/bootstrap.sh"
    report["preflight"] = run(["sh", str(bootstrap), "preflight"])
    try:
        SecretBroker(mode="keychain")
        report["system_secret_store"] = {"available": True}
    except Exception as exc:
        report["system_secret_store"] = {
            "available": False,
            "error": type(exc).__name__,
            "detail": str(exc).splitlines()[0],
        }

    service = BashAuthorizationService(
        workspace=lambda: workspace,
        frame_id=lambda: "wsl-parity-probe",
        audit=lambda **fields: None,
    )

    def authorize(method, args):
        handlers = {
            "authorize_bash": service.authorize,
            "consume_bash_authorization": service.consume,
            "record_bash_result": service.record_result,
        }
        return handlers[method](decode_args(args)[0])

    host = build_host(lambda method, args: None, bash_authorizer=authorize)
    (workspace / "values.sh").write_text("value=parity-ok\n", encoding="utf-8")
    report["host_bash"] = {}
    for label, command in (
        ("posix", "[ 1 = 1 ] && printf parity-ok"),
        ("bash_conditional", "[[ 1 = 1 ]] && printf parity-ok"),
        ("source", 'source ./values.sh && printf "$value"'),
    ):
        result = host.bash(command)
        report["host_bash"][label] = {
            key: result[key] for key in ("exit_code", "stdout", "stderr")
        }

    marker = windows_scratch / "marker.txt"
    marker.write_text("original", encoding="utf-8")
    translated = run(["wslpath", "-w", str(marker)])
    if translated["exit_code"]:
        raise RuntimeError("wslpath could not translate the probe's own marker")
    windows_marker = translated["stdout"].strip().replace("'", "''")
    powershell = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    report["windows_interop"] = {"tested": False}
    if Path(powershell).is_file() and report["preflight"]["exit_code"] == 0:
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"[IO.File]::WriteAllText('{windows_marker}', 'windows-interop-write')",
        ]
        with Kernel(cwd=str(workspace)) as kernel:
            first = kernel.execute("value = 41; print(value + 1)")
            second = kernel.execute("print(value)")
            result = kernel.execute(
                "import subprocess\n"
                f"p = subprocess.run({command!r}, capture_output=True, "
                "text=True, timeout=15)\n"
                "print(p.returncode, p.stdout, p.stderr)"
            )
            recovery = kernel.execute("print(value + 2)")
        report["windows_interop"] = {
            "tested": True,
            "stdout": result.get("stdout"),
            "error": result.get("error"),
            "windows_marker_changed": marker.read_text("utf-8") != "original",
            "python_stdout": first.get("stdout"),
            "persistent_stdout": second.get("stdout"),
            "recovery_stdout": recovery.get("stdout"),
        }

    # Execute the real source-copy step, not a hand-rewritten rsync command.
    builder = (ROOT / "scripts/build_linux_bundle.sh").read_text("utf-8")
    source_copy = builder[
        builder.index("rsync -a") : builder.index('cp "$REPO_ROOT/README.md"')
    ]
    copied = scratch / "copied-src"
    copied.mkdir()
    report["bundle_source_copy"] = run(
        ["bash", "-c", source_copy],
        env={**os.environ, "REPO_ROOT": str(ROOT), "SRC": str(copied)},
    )
    report["copied_default_ui"] = (
        copied / "openai4s/server/webui/dist/index.html"
    ).is_file()
    sys.path.insert(0, str(ROOT / "scripts"))
    from bundle_contract import check_sources

    try:
        report["source_verifier"] = {"passed": True, "skills": check_sources(copied)}
    except Exception as exc:
        report["source_verifier"] = {"passed": False, "error": str(exc)}

    # A stub interpreter proves wrapper environment routing, not Python ABI.
    match = re.search(r"<<'CLI'\n(.*?)\nCLI\n", builder, re.S)
    if match is None:
        raise RuntimeError("could not locate the generated CLI template")
    app = scratch / "wrapper-fixture"
    (app / "bin").mkdir(parents=True)
    (app / "runtime/bin").mkdir(parents=True)
    wrapper = app / "bin/openai4s"
    wrapper.write_text(match.group(1) + "\n", encoding="utf-8")
    wrapper.chmod(0o755)
    interpreter = app / "runtime/bin/python3"
    interpreter.write_text(
        '#!/bin/sh\nprintf "resolved-python3="\ncommand -v python3\n'
        'printf "resolved-python="\ncommand -v python || true\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    report["generated_cli_fixture"] = run(
        [str(wrapper)], env={**os.environ, "PATH": "/usr/bin:/bin"}
    )

    old = scratch / "data/app/OpenAI4S-probe-linux-x86_64/bin/openai4s"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\necho old-install\n", encoding="utf-8")
    old.chmod(0o755)
    broken = scratch / "corrupt.tar.gz"
    broken.write_bytes(b"not-an-archive")
    report["corrupt_archive_install"] = run(
        [
            "sh",
            str(bootstrap),
            "install",
            str(broken),
            hashlib.sha256(broken.read_bytes()).hexdigest(),
            "OpenAI4S-probe-linux-x86_64",
        ]
    )
    report["old_cli_survived"] = old.exists()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux" or "microsoft" not in platform.release().lower():
        parser.error("run this diagnostic inside WSL2")
    if os.getuid() == 0:
        parser.error("run as an ordinary WSL user, not root")
    windows_scratch = args.windows_scratch.resolve(strict=True)
    if not windows_scratch.is_dir():
        parser.error("--windows-scratch must be an existing directory")
    output = args.output.resolve() if args.output else None
    original_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix="o4s-parity-") as scratch:
            with tempfile.TemporaryDirectory(
                prefix="o4s-parity-", dir=windows_scratch
            ) as windows_tmp:
                report = probe(Path(scratch), Path(windows_tmp))
    finally:
        os.chdir(original_cwd)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
