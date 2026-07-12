"""Fail-closed smoke for the real macOS Seatbelt kernel boundary.

This is intentionally not part of default pytest collection.  Scheduled CI
runs it on a GitHub-hosted macOS runner with ``OPENAI4S_KERNEL_SANDBOX=enforce``
so a missing/degraded sandbox is a hard failure rather than a warning.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
import uuid
from pathlib import Path

from openai4s.kernel import Kernel


def main() -> int:
    if platform.system() != "Darwin":
        raise RuntimeError("macOS sandbox smoke must run on Darwin")
    os.environ["OPENAI4S_KERNEL_SANDBOX"] = "enforce"
    # This marker must be removed by the child environment allowlist, including
    # from a subprocess spawned by the scientific worker.
    os.environ["OPENAI4S_LLM_API_KEY"] = "nightly-secret-marker"
    root = Path(tempfile.mkdtemp(prefix="openai4s-macos-sandbox-"))
    workspace = root / "workspace"
    workspace.mkdir()
    outside = Path(tempfile.gettempdir()) / f"openai4s-outside-{uuid.uuid4().hex}"
    code = f"""
import json, os, socket, subprocess, sys
checks = {{}}
try:
    open({str(outside)!r}, "w", encoding="utf-8").write("escape")
    checks["outside_write_blocked"] = False
except Exception:
    checks["outside_write_blocked"] = True
try:
    sock = socket.socket()
    sock.settimeout(0.2)
    sock.connect(("127.0.0.1", 9))
    checks["network_blocked"] = False
except PermissionError:
    checks["network_blocked"] = True
except OSError as error:
    checks["network_blocked"] = getattr(error, "errno", None) in (1, 13, 45, 65)
finally:
    try: sock.close()
    except Exception: pass
open("inside.txt", "w", encoding="utf-8").write("ok")
checks["workspace_write"] = os.path.exists("inside.txt")
child = subprocess.run(
    [sys.executable, "-c", "import os; print(os.environ.get('OPENAI4S_LLM_API_KEY', ''))"],
    capture_output=True, text=True, check=False,
)
checks["subprocess_secret_absent"] = child.returncode == 0 and not child.stdout.strip()
print(json.dumps(checks, sort_keys=True))
"""
    try:
        with Kernel(cwd=str(workspace)) as kernel:
            status = kernel.sandbox_status
            if not status.get("enforced") or not status.get("self_test_passed"):
                raise RuntimeError(f"sandbox was not enforced: {status}")
            result = kernel.execute(code, origin="system")
        if result.get("error"):
            raise RuntimeError(f"sandbox smoke cell failed: {result['error']}")
        lines = [line for line in str(result.get("stdout") or "").splitlines() if line]
        checks = json.loads(lines[-1]) if lines else {}
        expected = {
            "network_blocked": True,
            "outside_write_blocked": True,
            "subprocess_secret_absent": True,
            "workspace_write": True,
        }
        if checks != expected:
            raise RuntimeError(f"sandbox smoke mismatch: {checks!r}")
        print(json.dumps({"ok": True, "sandbox": status, "checks": checks}))
        return 0
    finally:
        try:
            outside.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
