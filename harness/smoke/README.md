# Harness smoke checks

[中文说明](README_zh.md)

Small checks that cross a real runtime or platform boundary, which is why they only run when you ask for them. The offline core never imports this package, and default pytest collection never picks it up.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Marks the opt-in smoke package; importing it runs nothing. |
| [`macos_sandbox.py`](macos_sandbox.py) | The Darwin/Seatbelt check, and it fails closed: the sandbox must come out enforced and pass its self-test, or the program raises. It then proves from inside the worker that writes outside the workspace and outbound network are blocked, that a workspace write still works, and that a subprocess the worker spawns cannot see the daemon's secrets. |
| [`linux_sandbox.py`](linux_sandbox.py) | The same four boundaries under bubblewrap. The frozen matrix gates Linux beta on a real enforced-sandbox E2E rather than a probe that degrades; macOS had that proof and Linux did not, so its tier rested on nothing. It asserts the backend really is bubblewrap — a run that fell back and still passed would be reporting on a boundary it never tested. |
| [`sandbox_boundary.py`](sandbox_boundary.py) | The checks both OS smokes share: no write outside the workspace, no socket, a writable workspace, and no daemon credential reaching a spawned subprocess. Shared rather than copied, because two copies drift until one platform quietly stops checking what the other still does. |
| [`.gitkeep`](.gitkeep) | Keeps the smoke extension directory present. |

Run the macOS check on Darwin only, in the scheduled or explicitly dispatched environment it was written for. It raises rather than warns when the platform is wrong or the sandbox comes back degraded. See the [ground rules](../README.md#ground-rules) in the Harness root.
