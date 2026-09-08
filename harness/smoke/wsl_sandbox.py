"""Real WSL2 boundary, secure storage and persistent interrupt acceptance.

Run as an ordinary WSL user: python -m harness.smoke.wsl_sandbox.
Only synthetic secrets and temporary files are used. Never silently skips.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from harness.smoke.linux_bwrap_interrupt import _interrupt_and_continue
from harness.smoke.sandbox_boundary import run_boundary_smoke
from openai4s.kernel import Kernel
from openai4s.kernel.preinstall import run_confined_probe
from openai4s.security import wsl
from openai4s.security.secret_broker import WindowsDPAPIBackend
from openai4s.tools.dynamic import DynamicToolManifest, DynamicToolWorker


def main() -> int:
    if not wsl.is_wsl() or os.getuid() == 0:
        raise RuntimeError("this smoke requires an ordinary WSL2 user")
    powershell = wsl.powershell_path()
    if not powershell:
        raise RuntimeError("Windows interop must work on the Host for this smoke")
    run_boundary_smoke(
        label="wsl", expected_backend="bubblewrap", forbid_raw_network=True
    )
    probe = run_confined_probe(
        [sys.executable, "-c", "import os; print(os.getpid())"], timeout=10
    )
    if probe.returncode or probe.stdout.strip() != b"2":
        raise RuntimeError(f"confined environment probe failed: {probe.stderr!r}")
    backend = WindowsDPAPIBackend()
    namespace = uuid.uuid4().hex
    canary = "synthetic WSL secure-storage \u79d1\u5b66\nvalue"
    try:
        backend.put(namespace, "smoke", "canary", canary)
        # A second instance invokes a fresh Windows process; this is not a
        # same-object memory cache masquerading as persistent secure storage.
        if WindowsDPAPIBackend().get(namespace, "smoke", "canary") != canary:
            raise RuntimeError("Windows secure store did not persist the canary")
    finally:
        backend.delete(namespace, "smoke", "canary")
    if backend.get(namespace, "smoke", "canary") is not None:
        raise RuntimeError("Windows secure store did not delete the canary")

    with tempfile.TemporaryDirectory(prefix="o4s-wsl-smoke-") as scratch:
        workspace = Path(scratch) / "workspace"
        workspace.mkdir()
        manifest = DynamicToolManifest(
            name="wsl_sum",
            description="WSL acceptance",
            input_schema={},
            output_schema={},
            implementation="def execute(args): return sum(args['values'])",
            imports=(),
            permissions=(),
            scope="session",
            session_id="wsl-smoke",
            ttl_s=60,
            created_at=0,
            expires_at=60,
            manifest_id="wsl-smoke",
        )
        if DynamicToolWorker(workspace).invoke(manifest, {"values": [20, 22]}) != 42:
            raise RuntimeError("confined dynamic tool failed")
        shutil.copyfile(powershell, workspace / "copied.exe")
        (workspace / "copied.exe").chmod(0o755)
        shutil.copyfile("/init", workspace / "copied-init")
        (workspace / "copied-init").chmod(0o755)
        host_pid = os.getpid()
        interop_sockets = [str(path) for path in Path("/run/WSL").glob("*_interop")]
        if not interop_sockets:
            # The per-session sockets are the headline claim of this smoke. An
            # empty glob makes the enumerate() loop below contribute zero
            # checks, so `all(...)` would still pass having proven nothing.
            raise RuntimeError(
                "no live WSL interop socket to test the boundary against"
            )
        with Kernel(cwd=str(workspace)) as kernel:
            code = f"""
import errno, json, os, socket, subprocess
from pathlib import Path
checks = {{}}
def denied_command(argv):
    try:
        return subprocess.run(argv, capture_output=True, timeout=5).returncode != 0
    except OSError:
        return True
checks['windows_executable'] = denied_command([{powershell!r}, '-NoProfile', '-Command', 'exit 0'])
checks['copied_executable'] = denied_command(['./copied.exe', '-NoProfile', '-Command', 'exit 0'])
checks['copied_loader'] = denied_command(['./copied-init', {powershell!r}, '-NoProfile', '-Command', 'exit 0'])
checks['host_proc_alias'] = not Path('/proc/{host_pid}/root/init').exists()
checks['interop_loader'] = not os.access('/init', os.X_OK)
checks['windows_mount'] = not Path({powershell!r}).exists()
for index, address in enumerate({interop_sockets!r}):
    for prefix in ('', '/proc/{host_pid}/root'):
        with socket.socket(socket.AF_UNIX) as client:
            try:
                client.connect(prefix + address)
                checks[f'interop_socket_{{index}}_{{prefix}}'] = False
            except OSError:
                checks[f'interop_socket_{{index}}_{{prefix}}'] = True
try:
    connection = socket.socket(40, socket.SOCK_STREAM)
except OSError as exc:
    checks['hyperv_socket'] = exc.errno == errno.EPERM
else:
    connection.close()
    checks['hyperv_socket'] = False
# seccomp classifies syscalls, not ring submissions: IORING_OP_SOCKET would
# open AF_VSOCK without the socket rule ever being consulted.
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
params = ctypes.create_string_buffer(256)
ring = libc.syscall(425, 8, params)
if ring < 0:
    checks['io_uring_denied'] = ctypes.get_errno() in (errno.EPERM, errno.ENOSYS)
else:
    os.close(ring)
    checks['io_uring_denied'] = False
checks['private_pid'] = os.getpid() == 2 and os.getppid() == 1
marker = 41
print(json.dumps(checks))
"""
            result = kernel.execute(code)
            if result.get("error"):
                raise RuntimeError(f"WSL boundary probe failed: {result['error']}")
            checks = json.loads(result["stdout"])
            if not checks or not all(value is True for value in checks.values()):
                raise RuntimeError(f"WSL boundary failed: {checks}")
            interrupt = _interrupt_and_continue(
                kernel=kernel,
                label="wsl",
                long_cell="import time\nprint('wsl-started', flush=True)\ntime.sleep(60)",
                continuation="print(marker + 1)",
                continuation_expected="42",
            )
    print(
        json.dumps(
            {
                "wsl_boundary": checks,
                "secure_store": "windows-dpapi",
                "confined_probe": "passed",
                "dynamic_tool": "passed",
                "interrupt": interrupt,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
