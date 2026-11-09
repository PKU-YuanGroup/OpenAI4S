"""Run the existing installed-runtime scenario on a macOS app or WSL install."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import tempfile

from installed_runtime_smoke import Runtime, Scenario, interpreter_version, normalized_arch, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mac-app", type=Path)
    group.add_argument("--wsl-app", type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    app = (args.mac_app or args.wsl_app).resolve(strict=True)
    if args.mac_app:
        assert platform.system() == "Darwin"
        resources = app / "Contents/Resources"
        python = resources / "runtime/bin/python3"
        cli = resources / "runtime/bin/openai4s"
        serve = [str(app / "Contents/MacOS/OpenAI4S")]
        kind = "macOS CFBundleExecutable"
    else:
        assert platform.system() == "Linux"
        assert "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
        python = app / "runtime/bin/python3"
        cli = app / "bin/openai4s"
        serve = [str(cli), "serve", "--no-open"]
        kind = "WSL2 installed bundle"
    assert python.is_file() and cli.is_file() and Path(serve[0]).is_file()
    root = Path(tempfile.mkdtemp(prefix="openai4s platform smoke "))
    runtime = Runtime(
        target="bundle", artifact_sha256=sha256_file(args.artifact),
        platform_name=platform.system().lower(), arch=normalized_arch(platform.machine()),
        python_version=interpreter_version(python), origin_root=app,
        serve_argv=serve, stop_argv=[str(cli), "stop"], cwd=root,
    )
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "PLAYWRIGHT_BROWSERS_PATH") if k in os.environ}
    env.update(OPENAI4S_NO_OPEN="1", OPENAI4S_OPENMHS="off", OPENAI4S_SKIP_DOTENV="1", OPENAI4S_KERNEL_SANDBOX="enforce")
    report = Scenario(runtime=runtime, work_root=root, browser=args.browser, env_base=env).run()
    report.update(launch_path=kind, tested_installation=str(app), host_os_release=platform.release())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
