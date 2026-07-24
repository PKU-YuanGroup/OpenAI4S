# Supported platforms

The frozen matrix ([`v02-decisions.md`](v02-decisions.md), 8.5). This page says
what the code enforces today, and names the gates that are not yet met —
a support claim nobody has to take on faith is the only kind worth publishing.

| Platform | Tier | Kernel | OS sandbox | Gate |
| --- | --- | --- | --- | --- |
| macOS (Apple Silicon) | **stable** | runs | Seatbelt, enforced and smoke-tested nightly | Developer ID signing + notarization — **not yet done** |
| macOS (Intel) | stable | runs | Seatbelt | the `.dmg` is Apple Silicon only; install from PyPI |
| Linux (x86_64 / arm64) | **beta** | runs | bubblewrap, enforced and smoke-tested nightly | enforced-bubblewrap E2E — **met** (`harness/smoke/linux_sandbox.py`) |
| Windows (native) | **unsupported** | **refused** | none exists | not planned; use WSL2, which reports as Linux |
| Anything else | unsupported | **refused** | — | — |

## What "unsupported" means here

It means the kernel **refuses to start**, not that it prints a warning and
tries anyway. Before this, a native Windows install printed one line during
onboarding and then went on to spawn a kernel — and a program that warns and
proceeds has made a different promise from one that refuses. The first leaves a
scientist to discover the problem from a half-working analysis, which is
precisely the failure a product built on trustworthy results cannot afford.

The refusal lives at the kernel spawn path
([`openai4s/platform_support.py`](../openai4s/platform_support.py)), which every
Python and R kernel passes through, so there is no route to a subprocess that
skips it. The message names both the reason (POSIX subprocesses, and no Windows
sandbox backend) and the way out (WSL2).

## Why Linux is beta and macOS is stable

Not a difference in the code — the same kernel and the same host RPC run on
both. The tiers differ in what has been *proven*:

- macOS ships as a signed, notarized `.dmg`, which is a distribution promise on
  top of a technical one. **That signing and notarization has not happened
  yet**, so the stable tier is the target, not the current state.
- Linux is gated on a real enforced-bubblewrap end-to-end test rather than on a
  probe that degrades. That test now exists and runs nightly; it asserts the
  backend really is bubblewrap, so a runner that silently fell back cannot
  report a pass for a boundary it never tested.

Both smokes check the same four boundaries, from one shared implementation
([`harness/smoke/sandbox_boundary.py`](../harness/smoke/sandbox_boundary.py)):
a cell cannot write outside its workspace, cannot open a socket, can write
inside its workspace, and cannot leak the daemon's credentials into a
subprocess it spawns. They are shared rather than copied because two copies
drift until one platform quietly stops checking what the other still does.

## Degraded sandboxes

`OPENAI4S_KERNEL_SANDBOX` takes `auto` (default), `enforce`, or `off`. On
`auto`, a missing backend degrades **visibly** — a runtime warning and a
machine-readable degraded status — rather than silently. `enforce` fails closed
before a worker starts. The nightly smokes run under `enforce`, which is why a
missing bubblewrap is a CI failure rather than a shrug.
