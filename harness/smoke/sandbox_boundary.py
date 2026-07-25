"""The kernel boundary checks both OS sandboxes have to pass.

`macos_sandbox.py` proved a real Seatbelt boundary on a scheduled macOS runner.
The frozen platform matrix (docs/v02-decisions.md, 8.5) puts Linux at beta
"after enforced bubblewrap E2E", and gates that tier on a real enforced-sandbox
test rather than on a probe that degrades -- so Linux needs the same proof, and
there was none.

The checks are identical because the promise is: whatever the backend, a cell
cannot write outside its workspace, cannot open a socket, can write inside its
workspace, and cannot leak the daemon's credentials into a subprocess it
spawns. Only the platform assertion and the backend name differ, so the body
lives here rather than in two copies that would drift -- and a drifted copy is
how one platform quietly stops checking what the other still does.

Not part of default pytest collection: it needs a real enforced sandbox, which
a developer laptop may not have, and a check that degrades to a warning is
exactly what the frozen decision refuses.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path


def boundary_probe(outside_path: Path) -> str:
    """The cell run inside the kernel. Reports what the boundary allowed."""
    return f"""
import json, os, socket, subprocess, sys
checks = {{}}
try:
    open({str(outside_path)!r}, "w", encoding="utf-8").write("escape")
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
    checks["network_blocked"] = getattr(error, "errno", None) in (1, 13, 45, 65, 101)
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


EXPECTED = {
    "network_blocked": True,
    "outside_write_blocked": True,
    "subprocess_secret_absent": True,
    "workspace_write": True,
}


def run_boundary_smoke(*, label: str, expected_backend: str | None = None) -> int:
    """Enforce a real sandbox and assert the four boundaries hold.

    `expected_backend` is checked when given, so a runner that silently fell
    back to a different mechanism fails loudly instead of reporting a pass for
    a boundary it did not test.
    """
    from openai4s.kernel import Kernel

    os.environ["OPENAI4S_KERNEL_SANDBOX"] = "enforce"
    # Removed by the child-environment allowlist, including from a subprocess
    # the scientific worker spawns. Its survival anywhere is a leak.
    os.environ["OPENAI4S_LLM_API_KEY"] = f"{label}-secret-marker"

    root = Path(tempfile.mkdtemp(prefix=f"openai4s-{label}-sandbox-"))
    workspace = root / "workspace"
    workspace.mkdir()
    outside = Path(tempfile.gettempdir()) / f"openai4s-outside-{uuid.uuid4().hex}"

    try:
        with Kernel(cwd=str(workspace)) as kernel:
            status = kernel.sandbox_status
            if not status.get("enforced") or not status.get("self_test_passed"):
                raise RuntimeError(f"sandbox was not enforced: {status}")
            if expected_backend and status.get("backend") != expected_backend:
                raise RuntimeError(
                    f"expected the {expected_backend} backend, got "
                    f"{status.get('backend')!r}; a pass here would describe a "
                    "boundary this run never tested"
                )
            result = kernel.execute(boundary_probe(outside), origin="system")
        if result.get("error"):
            raise RuntimeError(f"sandbox smoke cell failed: {result['error']}")
        lines = [line for line in str(result.get("stdout") or "").splitlines() if line]
        checks = json.loads(lines[-1]) if lines else {}
        if checks != EXPECTED:
            raise RuntimeError(f"sandbox smoke mismatch: {checks!r}")
        print(json.dumps({"ok": True, "sandbox": status, "checks": checks}))
        return 0
    finally:
        try:
            outside.unlink()
        except FileNotFoundError:
            pass


__all__ = ["EXPECTED", "boundary_probe", "run_boundary_smoke"]
