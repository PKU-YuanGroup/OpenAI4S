# Supported platforms

The frozen matrix ([`v02-decisions.md`](v02-decisions.md), 8.5). This page says
what the code enforces today, and names the gates that are not yet met —
a support claim nobody has to take on faith is the only kind worth publishing.

| Platform | Tier | Kernel | OS sandbox | Gate |
| --- | --- | --- | --- | --- |
| macOS (Apple Silicon) | **stable** | runs | Seatbelt, enforced and smoke-tested nightly | Developer ID signing + notarization — **not yet done** |
| macOS (Intel) | stable | runs | Seatbelt | the `.dmg` is Apple Silicon only; install from PyPI |
| Linux (x86_64 / arm64) | **beta** | runs | bubblewrap, enforced | enforced-bubblewrap E2E — **written, not continuously verified** (`harness/smoke/linux_sandbox.py`; see below) |
| Windows (native) | **unsupported** | **refused** | none exists | not planned; use WSL2, which reports as Linux |
| Anything else | unsupported | **refused** | — | — |

## What ships, per platform

A support tier is a claim about the code. This is the separate question of what
a person can actually download, and the two are not the same thing — the Windows
row above says "unsupported" and the Windows row below says a package exists,
because that package does not run OpenAI4S on Windows.

| Download | Built by | Verified by | What it is |
| --- | --- | --- | --- |
| `OpenAI4S-<v>-macos-arm64.dmg` | [`scripts/build_macos_dmg.sh`](../scripts/build_macos_dmg.sh) | `verify_macos_bundle.py` | An `.app` with an embedded relocatable CPython and the pre-baked science stack. Ad-hoc signed, not notarized. |
| `OpenAI4S-<v>-linux-x86_64.tar.gz` | [`scripts/build_linux_bundle.sh`](../scripts/build_linux_bundle.sh) | `verify_linux_bundle.py` | The same payload as a relocatable directory, plus a `.desktop` template and a per-user `install.sh`. Unpack anywhere and run `./OpenAI4S`. |
| `OpenAI4S-<v>-windows-x86_64.zip` | [`scripts/build_windows_zip.sh`](../scripts/build_windows_zip.sh) | `verify_windows_zip.py` | A Windows launcher wrapped around **that exact Linux tarball**. It requires WSL2 + working bubblewrap 0.8.0+, installs offline, and opens the authenticated URL returned by the WSL CLI. Not a native Windows build; see below and the [Windows/WSL2 guide](windows-wsl.md). |
| `openai4s-<v>-py3-none-any.whl` | `uv build` | `verify_release_artifacts.py` | The zero-dependency wheel, for any supported platform with its own Python. |
| *(none yet — build it yourself)* | [`Dockerfile`](../Dockerfile) | [`scripts/container_smoke.sh`](../scripts/container_smoke.sh) | A Linux container image of the daemon and workbench. No registry publishes it, so this row names no download: `docker build -t openai4s:local .` from a checkout. Multi-arch is free here in a way it is not for the bundles — the wheel is `py3-none-any` and the science stack has manylinux `aarch64` wheels — but nothing has built an arm64 image either, so that is untested rather than promised. See [docker.md](docker.md). |

Only `x86_64` slices are published, the same way the `.dmg` is Apple Silicon
only. Both build scripts take `ARCH=aarch64` and produce a correct arm64 bundle,
but nothing publishes one yet, so arm64 Linux and Windows-on-ARM install from
PyPI (`pip install openai4s`). Naming an architecture we do not actually upload
would put the burden of discovering that on the person downloading it.

The Linux bundle is deliberately not an AppImage. An AppImage is a squashfs
mounted through FUSE, and this program's job is to spawn subprocesses under
bubblewrap; nesting a user namespace inside a FUSE mount is the combination that
fails on hardened and containerised hosts, and it would fail at *cell execution*
time — long after the app looked like it had started successfully.
## Python versions

| Version | `requires-python` | Classifier | CI offline suite | Ships in the `.dmg` |
| --- | --- | --- | --- | --- |
| 3.10 | admitted (the floor) | yes | yes | no |
| 3.11 | admitted | yes | no — see below | no |
| 3.12 | admitted | yes | yes (+ science + chemistry extras) | no |
| 3.13 | admitted | yes | yes | **yes** |
| 3.14+ | admitted by `>=3.10` | no | no | no |

Three files used to each claim something different, and the disagreement was
invisible because nothing compared them. `requires-python` said `>=3.10`, the
classifiers stopped at 3.12, CI ran 3.10 and 3.12 — and
[`build_macos_dmg.sh`](../scripts/build_macos_dmg.sh) embedded **3.13**. So the
build that reaches the most end users, the double-clickable one, ran on the
single interpreter nothing in the repository exercised, on a version the
package did not claim to support. A 3.13-only failure would have shipped green,
because no job could see it.

3.13 is now classified and tested. The reconciliation is enforced by
[`tests/test_platform_support.py`](../tests/test_platform_support.py), which
reads all three files rather than restating them: a matrix written down in
prose is correct on the day it is written.

**3.11 is claimed and not directly tested, on purpose.** CI runs the floor
(3.10), the shipped interpreter (3.13), and 3.12 with the optional science and
chemistry extras. A version bracketed on both sides by tested ones is a
different risk from one outside the tested range entirely, which is what 3.13
was. Naming the gap is the point — it is a stated cost, not an oversight, and
the test that enforces the rest deliberately does not enforce this.

**3.14 and later are admitted by `>=3.10` and are not claimed.** The bound is
left open rather than capped so a new interpreter does not block installation,
but nothing here has run on one.

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

## The Windows package, and why it is not a contradiction

There is a Windows download, and native Windows is still refused. Both are true
because the package does not run OpenAI4S on Windows: it is a launcher that
installs the Linux bundle into the user's WSL2 distribution, starts the daemon
there, asks `openai4s url` for the authenticated first-visit URL, and opens the
Windows browser at the forwarded localhost port. WSL2
reports as `linux`, so what runs is the supported build, unmodified.

Making the documented way out double-clickable is the whole value. The
alternative — shipping something that starts on native Windows — would move the
"warns and proceeds" failure from the kernel to the installer, where it is
harder to see and lands on a machine nobody here can debug.

Three properties keep the package honest, and
[`scripts/verify_windows_zip.py`](../scripts/verify_windows_zip.py) fails the
build on each:

- it contains no Windows executable (`.exe`, `.dll`, `.pyd`), so there is
  nothing in it that *could* start a native kernel;
- the launcher's only execution path goes through `wsl.exe`;
- a WSL **1** distribution is refused rather than used. WSL 1 emulates Linux
  syscalls and has no user namespaces, so bubblewrap cannot start and cells
  would run unisolated — the exact silent degradation the tiers above exist to
  rule out.

- bubblewrap must be at least 0.8.0 and must pass a preflight using the same
  lifecycle, IPC, UTS, and network namespace flags as real Cells; the packaged
  daemon starts with `OPENAI4S_KERNEL_SANDBOX=enforce`.
- the Windows launcher never opens the bare root URL, which local authentication
  intentionally answers with 401; it opens only the URL returned by
  `openai4s url`, then the browser exchanges its query bootstrap for a cookie.

## Why Linux is beta and macOS is stable

Not a difference in the code — the same kernel and the same host RPC run on
both. The tiers differ in what has been *proven*:

- macOS ships as a signed, notarized `.dmg`, which is a distribution promise on
  top of a technical one. **That signing and notarization has not happened
  yet**, so the stable tier is the target, not the current state.
- Linux is gated on a real enforced-bubblewrap end-to-end test rather than on a
  probe that degrades. That test exists and asserts the backend really is
  bubblewrap, so a host that silently fell back cannot report a pass for a
  boundary it never tested. **It is not running in CI**, for the reason below,
  so the Linux tier currently rests on manual runs.

Both smokes check the same four boundaries, from one shared implementation
([`harness/smoke/sandbox_boundary.py`](../harness/smoke/sandbox_boundary.py)):
a cell cannot write outside its workspace, cannot open a socket, can write
inside its workspace, and cannot leak the daemon's credentials into a
subprocess it spawns. They are shared rather than copied because two copies
drift until one platform quietly stops checking what the other still does.

## Why the Linux smoke is not in CI

A GitHub-hosted runner cannot run it. `bwrap` creates its network namespace and
then fails to bring up the loopback interface inside it:

```
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

That is the runner's own confinement of unprivileged user namespaces, not a
defect in the sandbox and not something the code under test can influence. The
job was therefore red every night from the day it was added, and a check that
cannot pass is not evidence of anything — it is a signal everyone learns to
scroll past, which costs more than the absent check does.

So the claim is downgraded here instead of being propped up by a job that never
went green. To re-establish it, run the smoke on a Linux host that permits
unprivileged user namespaces:

```bash
OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox
```

Restoring it to CI needs a runner where that is possible — a self-hosted Linux
runner, or a container with the namespace permissions bwrap needs. Until one
exists, "beta" here means the boundary is implemented and asserted by a test
someone has to run, not one that runs itself.

## Degraded sandboxes

`OPENAI4S_KERNEL_SANDBOX` takes `auto` (default), `enforce`, or `off`. On
`auto`, a missing backend degrades **visibly** — a runtime warning and a
machine-readable degraded status — rather than silently. `enforce` fails closed
before a worker starts. The macOS nightly smoke runs under `enforce`, which is
why a missing Seatbelt is a CI failure rather than a shrug.

The container image is the ordinary case of that degradation rather than an
exception to it. An unprivileged container cannot give bubblewrap the user,
mount and network namespaces it wants — the same confinement that keeps
`harness.smoke.linux_sandbox` off GitHub-hosted runners — so `auto` warns once
and the container becomes the boundary. What that boundary does *not* replace
is specific: an unenforced sandbox drops the secret-read masks over
`<data_dir>/openai4s.db` and `<data_dir>/access-token` at the same time as the
network namespace, and a cell's working directory is two levels below that
token. [docker.md](docker.md) states the trade in full.
