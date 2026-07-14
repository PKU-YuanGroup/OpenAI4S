# Harness smoke checks

[中文说明](README_zh.md)

Small checks that cross a real runtime or platform boundary, which is why they only run when you ask for them. The offline core never imports this package, and default pytest collection never picks it up.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Marks the opt-in smoke package; importing it runs nothing. |
| [`macos_sandbox.py`](macos_sandbox.py) | The Darwin/Seatbelt check, and it fails closed: the sandbox must come out enforced and pass its self-test, or the program raises. It then proves from inside the worker that writes outside the workspace and outbound network are blocked, that a workspace write still works, and that a subprocess the worker spawns cannot see the daemon's secrets. |
| [`.gitkeep`](.gitkeep) | Keeps the smoke extension directory present. |

Run the macOS check on Darwin only, in the scheduled or explicitly dispatched environment it was written for. It raises rather than warns when the platform is wrong or the sandbox comes back degraded. See the [ground rules](../README.md#ground-rules) in the Harness root.
