# Harness smoke checks

[中文说明](README_zh.md)

Small checks that cross a real runtime or platform boundary, which is why they only run when you ask for them. The offline core never imports this package, and default pytest collection never picks it up.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Marks the opt-in smoke package; importing it runs nothing. |
| [`linux_bwrap_interrupt.py`](linux_bwrap_interrupt.py) | The real hosted-Linux process-identity check. It starts team-isolated Python and R kernels under bubblewrap, requires `--unshare-pid` plus `--info-fd`, verifies the command is PID 2 and has a pinned pidfd, interrupts a live long Cell, then proves the same persistent namespace answers another Cell. CI deliberately sets `OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1` so process-identity evidence is independent of network setup; this smoke therefore makes no network-isolation claim. Ubuntu 24.04 also denies an unprofiled bwrap's user namespace; CI loads the distribution's capability-stripping `bwrap-userns-restrict` profile instead of disabling the host-wide AppArmor restriction. |
| [`macos_sandbox.py`](macos_sandbox.py) | The Darwin/Seatbelt check, and it fails closed: the sandbox must come out enforced and pass its self-test, or the program raises. It then proves from inside the worker that writes outside the workspace and outbound network are blocked, that a workspace write still works, and that a subprocess the worker spawns cannot see the daemon's secrets. |
| [`linux_sandbox.py`](linux_sandbox.py) | The same four boundaries under bubblewrap. It asserts the backend really is bubblewrap — a run that fell back and still passed would be reporting on a boundary it never tested. Independent CI job on Ubuntu 24.04 (`linux-sandbox-full`): enforce mode, no raw-network override (that override is a hard failure). The check run is attested at the frozen SHA; the release workflow's `platform-checks` matrix still does not re-execute it until multiple scheduled greens plus a candidate SHA pass. See [`docs/platforms.md`](../../docs/platforms.md). |
| [`sandbox_boundary.py`](sandbox_boundary.py) | The checks both OS smokes share: no write outside the workspace, no socket, a writable workspace, and no daemon credential reaching a spawned subprocess. Shared rather than copied, because two copies drift until one platform quietly stops checking what the other still does. |
| [`.gitkeep`](.gitkeep) | Keeps the smoke extension directory present. |

Run the macOS check on Darwin only, in the scheduled or explicitly dispatched environment it was written for. The full Linux boundary check runs as its own CI job: `OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox`. The narrower interrupt proof runs in every CI workflow as `OPENAI4S_KERNEL_SANDBOX=enforce OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1 uv run python -m harness.smoke.linux_bwrap_interrupt`; the raw-network exception is why its success must never be reported as the full boundary passing. All three raise rather than warn when their required platform or sandbox is absent. See the [ground rules](../README.md#ground-rules) in the Harness root.
