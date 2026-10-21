# Supported platforms

The frozen matrix ([`v02-decisions.md`](v02-decisions.md), 8.5). This page says
what the code enforces today, and names the gates that are not yet met —
a support claim nobody has to take on faith is the only kind worth publishing.

| Platform | Tier | Kernel | OS sandbox | Gate |
| --- | --- | --- | --- | --- |
| macOS (Apple Silicon) | **stable** | runs | Seatbelt, enforced and smoke-tested nightly | **0.3.1 uses PyPI; no DMG is included.** Developer ID signing and notarization remain deferred. The release workflow uploads a DMG only with `macos_asset=notarized`; older v0.3.0/v0.2.0 DMGs are ad-hoc-signed previews. The tier describes the code. |
| macOS (Intel) | stable | runs | Seatbelt | install 0.3.1 from PyPI; older preview DMGs are Apple Silicon only |
| Linux (x86_64 / arm64) | **beta** | runs | bubblewrap, enforced | full filesystem/egress boundary runs in every CI (`harness/smoke/linux_sandbox.py`); private-PID Python/R interrupt is a separate job that allows raw networking; the release workflow does not re-execute the full smoke until multiple scheduled greens pass |
| Windows (native) | **unsupported** | **refused** | none exists | not planned; use WSL2, which reports as Linux |
| Anything else | unsupported | **refused** | — | — |

## What ships, per platform

A support tier is a claim about the code. This is the separate question of what
a person can actually download, and the two are not the same thing — the Windows
row above says "unsupported" and the Windows row below says a package exists,
because that package does not run OpenAI4S on Windows.

**Current release: [0.3.1](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.3.1).** GitHub carries the Linux and Windows packages, `SHA256SUMS`, and the evidence archive. Python distributions are on PyPI; containers are on public GHCR.

| Download | Built by | Verified by | What it is |
| --- | --- | --- | --- |
| macOS DMG: **not included in 0.3.1** | [`scripts/build_macos_dmg.sh`](../scripts/build_macos_dmg.sh), then [`scripts/notarize_macos_dmg.sh`](../scripts/notarize_macos_dmg.sh) | `verify_macos_bundle.py` | Developer ID signing and notarization are deferred. Install from [PyPI 0.3.1](https://pypi.org/project/openai4s/0.3.1/) on Apple Silicon or Intel. Older ad-hoc-signed previews remain on the [v0.3.0](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.3.0) and [v0.2.0](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.2.0) pages; they install those older versions. |
| [OpenAI4S-0.3.1-linux-x86_64.tar.gz](https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/v0.3.1/OpenAI4S-0.3.1-linux-x86_64.tar.gz) | [`scripts/build_linux_bundle.sh`](../scripts/build_linux_bundle.sh) | `verify_linux_bundle.py` | Embedded Python and the science stack in a relocatable directory, with a `.desktop` template and per-user `install.sh`. Verify the checksum, unpack, and run `./OpenAI4S`. |
| [OpenAI4S-0.3.1-windows-x86_64.zip](https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/v0.3.1/OpenAI4S-0.3.1-windows-x86_64.zip) | [`scripts/build_windows_zip.sh`](../scripts/build_windows_zip.sh) | `verify_windows_zip.py` | A complete ZIP containing a Windows launcher and **the same Linux tarball**. Requires WSL2, Ubuntu 24.04, and working bubblewrap 0.8.0+. Archive, launcher, and payload checks passed; full Windows/WSL2 runtime acceptance was not performed for 0.3.1. See the [Windows/WSL2 guide](windows-wsl.md). |
| [PyPI 0.3.1: wheel and source distribution](https://pypi.org/project/openai4s/0.3.1/) | `uv build` | `verify_release_artifacts.py` | The zero-dependency core for supported platforms with Python 3.10+. Install `openai4s[science]==0.3.1` for the optional science stack. These files are distributed through PyPI. |
| [GHCR](https://github.com/orgs/PKU-YuanGroup/packages/container/package/openai4s): `ghcr.io/pku-yuangroup/openai4s:0.3.1` | [`Dockerfile`](../Dockerfile), [`publish-image.yml`](../.github/workflows/publish-image.yml) | [`scripts/container_smoke.sh`](../scripts/container_smoke.sh) | Public linux/amd64 image; anonymous pulls are supported. Pin `0.3.1`; `latest` follows future releases. No arm64 image is published. See [docker.md](docker.md). |

Download [SHA256SUMS](https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/v0.3.1/SHA256SUMS)
to verify the desktop packages and the
[evidence archive](https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/v0.3.1/openai4s-0.3.1-evidence.zip).
The archive contains the build manifest, component inventory, SBOM, provenance,
and validation references.

Only `x86_64` Linux/Windows bundles are published. On arm64 Linux, use PyPI in a
virtual environment. Windows on ARM is unverified; native Windows remains
unsupported regardless of installation channel.

The Linux bundle is deliberately not an AppImage. An AppImage is a squashfs
mounted through FUSE, and this program's job is to spawn subprocesses under
bubblewrap; nesting a user namespace inside a FUSE mount is the combination that
fails on hardened and containerised hosts, and it would fail at *cell execution*
time — long after the app looked like it had started successfully.
## Python versions

| Version | `requires-python` | Classifier | CI offline suite | Preview DMG build (not shipped in 0.3.1) | Ships in the container |
| --- | --- | --- | --- | --- | --- |
| 3.10 | admitted (the floor) | yes | yes | no | no |
| 3.11 | admitted | yes | no — see below | no | no |
| 3.12 | admitted | yes | yes (+ science + chemistry extras) | no | no |
| 3.13 | admitted | yes | yes | **yes** | no |
| 3.14 | admitted | yes | yes | no | **yes** |
| 3.15+ | admitted by `>=3.10` | no | no | no | no |

Three files used to each claim something different, and the disagreement was
invisible because nothing compared them. `requires-python` said `>=3.10`, the
classifiers stopped at 3.12, CI ran 3.10 and 3.12 — and
[`build_macos_dmg.sh`](../scripts/build_macos_dmg.sh) embedded **3.13**. So the
build that reaches the most end users, the double-clickable one, ran on the
single interpreter nothing in the repository exercised, on a version the
package did not claim to support. A 3.13-only failure would have shipped green,
because no job could see it.

3.13 and 3.14 are now classified and tested. The reconciliation is enforced by
[`tests/test_platform_support.py`](../tests/test_platform_support.py), which
reads the package metadata, CI workflow, bundle scripts, and Dockerfile rather
than restating them: a matrix written down in prose is correct on the day it is
written.

**3.11 is claimed and not directly tested, on purpose.** CI runs the floor
(3.10), the `.dmg` interpreter (3.13), the container interpreter (3.14), and
3.12 with the optional science and chemistry extras. A version bracketed on
both sides by tested ones is a different risk from one outside the tested range
entirely. Naming the gap is the point — it is a stated cost, not an oversight,
and the test that enforces the rest deliberately does not enforce this.

**3.15 and later are admitted by `>=3.10` and are not claimed.** The bound is
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

- macOS distribution is gated on a signed, notarized `.dmg`, which would be a
  distribution promise on top of the technical one. No release has met that
  gate yet, so 0.3.1 omits the DMG. v0.2.0 and v0.3.0 each carry an ad-hoc-signed preview image, and
  v0.3.0's was built locally and attached outside the release workflow, because
  the Developer ID and notary credentials do not exist
  ([D11](v03-decisions.md)). The tier therefore rests on the technical half:
  Seatbelt enforced and smoke-tested nightly, and a PyPI install on either
  architecture. `scripts/notarize_macos_dmg.sh` is the sign →
  `notarytool submit --wait` → staple → `stapler validate` → `spctl` path.
  The release workflow input `macos_asset` defaults to `omit` so the workflow
  never uploads a preview DMG; `notarized` fail-fasts if the Developer ID +
  notary secret set is incomplete.
- Linux is gated on a real enforced-bubblewrap end-to-end test rather than on a
  probe that degrades. The full boundary test exists, asserts the backend
  really is bubblewrap, and now runs as an independent Ubuntu 24.04 CI job
  (`linux-sandbox-full`) under `OPENAI4S_KERNEL_SANDBOX=enforce` with the
  packaged AppArmor profile loaded. A raw-network override is a hard failure
  there. The narrower interrupt smoke still allows raw networking so its
  private-PID evidence does not depend on network-namespace setup. The release
  workflow's `platform-checks` matrix does **not** re-execute the full smoke
  until multiple scheduled greens plus a candidate SHA have passed
  (`PLATFORM_CHECKS_UNAVAILABLE`).

Both smokes check the same four boundaries, from one shared implementation
([`harness/smoke/sandbox_boundary.py`](../harness/smoke/sandbox_boundary.py)):
a cell cannot write outside its workspace, cannot open a socket, can write
inside its workspace, and cannot leak the daemon's credentials into a
subprocess it spawns. They are shared rather than copied because two copies
drift until one platform quietly stops checking what the other still does.

The separate
[`linux_bwrap_interrupt.py`](../harness/smoke/linux_bwrap_interrupt.py) smoke is
deliberately narrower. It retains team `KernelReadIsolation`, requires the
real `--unshare-pid` + `--info-fd` + procfs + pidfd production path, observes
Python and R as PID 2, interrupts a long-running Cell, and proves the same
kernel executes again. The hosted-runner job sets
`OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1`, so this is evidence for process identity
and SIGINT persistence only—not for the network boundary. The job pins Ubuntu
24.04 and loads its packaged `bwrap-userns-restrict` AppArmor profile, which
allows bwrap to construct the namespace and strips capabilities from the
executed worker. It does not disable the runner's host-wide unprivileged-userns
restriction.

## Why the full Linux boundary smoke is not a release-workflow platform check yet

The full filesystem-and-egress smoke now runs as its own CI job on Ubuntu
24.04, independent of the interrupt job, with the packaged
`bwrap-userns-restrict` AppArmor profile loaded and **without**
`OPENAI4S_KERNEL_ALLOW_RAW_NETWORK`. The check run is attested at the frozen
SHA (`ci-linux-sandbox-full`). That is not the same as re-executing it inside
the release workflow's `platform-checks` matrix: an unproven matrix leg once
made every publication unreachable. The row stays in
`release_gates.PLATFORM_CHECKS_UNAVAILABLE` until multiple scheduled greens
plus a candidate SHA have passed.

The interrupt job still deliberately allows raw networking so its
process-identity evidence does not depend on network setup; that exception is
why a green interrupt check is not this check.

```bash
OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox
```

## Degraded sandboxes

`OPENAI4S_KERNEL_SANDBOX` takes `auto` (default), `enforce`, or `off`. On
`auto`, a missing backend degrades **visibly** — a runtime warning and a
machine-readable degraded status — rather than silently. `enforce` fails closed
before a worker starts. The macOS nightly smoke runs under `enforce`, which is
why a missing Seatbelt is a CI failure rather than a shrug.

The container image is the ordinary case of that degradation rather than an
exception to it. An unprivileged container cannot give bubblewrap the user,
mount and network namespaces it wants, so `auto` warns once and the container
becomes the boundary. (The hosted CI runner is not a container: the
`linux-sandbox-full` job runs bubblewrap directly on the runner, with the
packaged `bwrap-userns-restrict` AppArmor profile loaded.) What that boundary does *not* replace
is specific: an unenforced sandbox drops the secret-read masks over
`<data_dir>/openai4s.db` and `<data_dir>/access-token` at the same time as the
network namespace, and a cell's working directory is two levels below that
token. [docker.md](docker.md) states the trade in full.
