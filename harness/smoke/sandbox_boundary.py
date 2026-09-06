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
    # Deliberately an unroutable *external* address (RFC 5737 TEST-NET-1), not
    # loopback. The two backends deny egress by different mechanisms, and only
    # an outward destination is refused by both. Seatbelt rejects the syscall
    # itself, so loopback answers EPERM there and the check appears to work;
    # bubblewrap's --unshare-net instead gives the cell a private network
    # namespace whose own loopback is up, so connecting to 127.0.0.1 returns
    # ECONNREFUSED -- a refusal by the absent listener, not by the boundary --
    # and the probe read a confined cell as unconfined. Unreachable-network
    # errnos mean the boundary held; a timeout (errno None) means the syscall
    # was allowed and packets simply went nowhere, which is not confinement.
    sock = socket.socket()
    sock.settimeout(0.2)
    sock.connect(("192.0.2.1", 9))
    checks["network_blocked"] = False
except PermissionError:
    checks["network_blocked"] = True
except OSError as error:
    checks["network_blocked"] = getattr(error, "errno", None) in (
        1, 13, 45, 65, 100, 101, 113,
    )
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

#: Values `openai4s.security.sandbox._parse_bool` accepts as true. Kept in
#: step with that parser so a host that set the override by any documented
#: spelling cannot slip past the full-boundary smoke.
_RAW_NETWORK_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RAW_NETWORK_FALSY = frozenset({"0", "false", "no", "off"})
_RAW_NETWORK_ENV = "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"


def raw_network_override_requested(env: dict[str, str] | None = None) -> bool:
    """Whether the compatibility switch that disables network confinement is on.

    The interrupt smoke sets this deliberately and therefore cannot prove
    egress denial. The full-boundary smoke must refuse that override rather
    than report a green run that never tested the socket.
    """
    source = os.environ if env is None else env
    raw = str(source.get(_RAW_NETWORK_ENV, "") or "").strip()
    if not raw:
        return False
    normalized = raw.lower()
    if normalized in _RAW_NETWORK_TRUTHY:
        return True
    if normalized in _RAW_NETWORK_FALSY:
        return False
    # A misspelt flag is not "off". The kernel would refuse it too; naming it
    # here keeps the full-boundary smoke from spawning a worker just to fail.
    raise RuntimeError(
        f"{_RAW_NETWORK_ENV}={raw!r} is not 1/0, true/false, yes/no, or on/off"
    )


def refuse_raw_network_override(
    *, label: str, env: dict[str, str] | None = None
) -> None:
    """Fail closed when the full boundary cannot prove network denial."""
    if raw_network_override_requested(env):
        source = os.environ if env is None else env
        raise RuntimeError(
            f"raw-network override is set "
            f"({_RAW_NETWORK_ENV}={source.get(_RAW_NETWORK_ENV)!r}); the full "
            f"{label} boundary smoke cannot prove network denial under that "
            "switch. Unset it. The interrupt job is the one that allows raw "
            "networking on purpose."
        )


def run_boundary_smoke(
    *,
    label: str,
    expected_backend: str | None = None,
    forbid_raw_network: bool = False,
) -> int:
    """Enforce a real sandbox and assert the four boundaries hold.

    `expected_backend` is checked when given, so a runner that silently fell
    back to a different mechanism fails loudly instead of reporting a pass for
    a boundary it did not test.

    `forbid_raw_network` is the full Linux filesystem/egress gate: a raw-network
    override would make `network_blocked` unprovable, so it is refused before a
    worker starts rather than recorded as a pass.
    """
    from openai4s.kernel import Kernel

    if forbid_raw_network:
        refuse_raw_network_override(label=label)

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
            if forbid_raw_network and status.get("network_policy") == "raw_allowed":
                raise RuntimeError(
                    f"sandbox reported network_policy=raw_allowed: {status}; "
                    "the full boundary smoke cannot pass under a raw-network "
                    "override"
                )
            result = kernel.execute(boundary_probe(outside), origin="system")
        if result.get("error"):
            raise RuntimeError(f"sandbox smoke cell failed: {result['error']}")
        lines = [line for line in str(result.get("stdout") or "").splitlines() if line]
        checks = json.loads(lines[-1]) if lines else {}
        if checks != EXPECTED:
            raise RuntimeError(f"sandbox smoke mismatch: {checks!r}")
        print(
            json.dumps(
                {
                    "ok": True,
                    "sandbox": status,
                    "checks": checks,
                    "raw_network_override": False if forbid_raw_network else None,
                }
            )
        )
        return 0
    finally:
        try:
            outside.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "EXPECTED",
    "boundary_probe",
    "raw_network_override_requested",
    "refuse_raw_network_override",
    "run_boundary_smoke",
]
