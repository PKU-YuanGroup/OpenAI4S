# Harness smoke checks

[中文说明](README_zh.md)

Small checks that cross a real runtime or platform boundary, which is why they only run when you ask for them. The offline core never imports this package, and default pytest collection never picks it up.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Marks the opt-in smoke package; importing it runs nothing. |
| [`macos_sandbox.py`](macos_sandbox.py) | The Darwin/Seatbelt check, and it fails closed: the sandbox must come out enforced and pass its self-test, or the program raises. It then proves from inside the worker that writes outside the workspace and outbound network are blocked, that a workspace write still works, and that a subprocess the worker spawns cannot see the daemon's secrets. |
| [`linux_sandbox.py`](linux_sandbox.py) | The same four boundaries under bubblewrap. It asserts the backend really is bubblewrap — a run that fell back and still passed would be reporting on a boundary it never tested. **Manual only:** a GitHub-hosted runner confines unprivileged user namespaces, so bwrap cannot bring up loopback inside its network namespace and the job failed every night without ever testing the code. See [`docs/platforms.md`](../../docs/platforms.md) for what the Linux tier now claims. |
| [`sandbox_boundary.py`](sandbox_boundary.py) | The checks both OS smokes share: no write outside the workspace, no socket, a writable workspace, and no daemon credential reaching a spawned subprocess. Shared rather than copied, because two copies drift until one platform quietly stops checking what the other still does. |
| [`.gitkeep`](.gitkeep) | Keeps the smoke extension directory present. |

Run the macOS check on Darwin only, in the scheduled or explicitly dispatched environment it was written for. Run the Linux check by hand on a host that permits unprivileged user namespaces: `OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox`. Both raise rather than warn when the platform is wrong or the sandbox comes back degraded. See the [ground rules](../README.md#ground-rules) in the Harness root.
