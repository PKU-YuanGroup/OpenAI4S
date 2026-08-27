# Windows launcher sources

[中文说明](README_zh.md)

The three files the Windows release package ships as its entry point.
[`../build_windows_zip.sh`](../build_windows_zip.sh) stages them beside the
Linux bundle it carries as a payload; nothing here is executed at build time.

They live as real files rather than heredocs inside the build script because
this is the code that runs on a machine none of our CI images resembles: it has
to be readable, diffable, and parseable on its own. The `windows-launcher` jobs
in [`../../.github/workflows/ci.yml`](../../.github/workflows/ci.yml) and
[`../../.github/workflows/release.yml`](../../.github/workflows/release.yml)
parse `openai4s.ps1` on a real Windows runner for exactly that reason — a syntax
error in it is invisible to every Linux and macOS job and would surface on the
first user's machine.

## Why this runs inside WSL2

Not a packaging shortcut. [`../../openai4s/platform_support.py`](../../openai4s/platform_support.py)
refuses to start a kernel on `win32`: the kernel spawns POSIX subprocesses, the
R channel rides file descriptors 3 and 4 through a shell redirection, and the OS
sandbox has no Windows backend. A package that started anyway would leave a
scientist to discover the problem from a half-working analysis — the same
"warns and proceeds" failure the refusal exists to prevent, one release channel
further downstream.

WSL2 reports as `linux`, which is a supported platform, so this package runs
the same program every other platform runs rather than an approximation of it.
The supported-platform matrix is [`../../docs/platforms.md`](../../docs/platforms.md).
The end-user walkthrough is [`../../docs/windows-wsl.md`](../../docs/windows-wsl.md).

## Files

| File | Purpose |
| --- | --- |
| `OpenAI4S.cmd` | The double-clickable entry point. Explorer opens a `.ps1` in an editor rather than running it, and the default execution policy blocks it even from a prompt, so this wrapper invokes PowerShell with `-ExecutionPolicy Bypass` for that one process and forwards its arguments and exit code. Ships CRLF. |
| `openai4s.ps1` | The Windows half: pick the WSL **2** distribution — one already holding OpenAI4S data pins the choice, so installing Ubuntu 24.04 for any other reason cannot strand existing sessions; otherwise Ubuntu 24.04 is preferred — propagate mainland package mirrors (`off` restores the official indexes) and an optional WSL-reachable proxy, install the payload, distinguish OpenAI4S from an unrelated port occupant, obtain the authenticated URL from `openai4s url`, and open the Windows browser. Every refusal names both the cause and the exact command that fixes it. Ships CRLF. |
| `bootstrap.sh` | The Linux half, run inside the distribution. It verifies the payload's checksum before unpacking (the archive crosses the 9p/DrvFs boundary, where a short read yields a truncated file rather than an error), installs idempotently, and starts the daemon fully detached. Ships LF — a carriage return here fails inside WSL with `bad interpreter`, and `../verify_windows_zip.py` refuses a package that has one. |

The bootstrap additionally proves bubblewrap 0.8.0+ accepts the same lifecycle,
IPC, UTS, and network namespace flags used by real Cells, writes the selected
mirror configuration — only over files carrying its managed marker, so a
user-edited `pip.conf` or condarc is preserved — and the `~/.local/bin/openai4s`
link, and starts the daemon with `OPENAI4S_KERNEL_SANDBOX=enforce` and browser
auto-open disabled. Setting either mirror selector to `off` explicitly restores
the corresponding official index; removing the management marker transfers
ownership to the user and preserves the complete file on later launches.
When WSL localhost forwarding is explicitly disabled, a wildcard
`OPENAI4S_HOST=0.0.0.0` remains the daemon bind while Windows uses the current
WSL IPv4 as its client address. IPv6 hosts are rejected up front because the
bundled HTTP server is IPv4-only.
Clash-style WSL Fake-IP DNS is detected automatically; RFC 2544 synthetic
answers are accepted only for built-in or explicitly approved public domains,
while literal and other private addresses remain behind the SSRF guard.

## Where this fits

None of this is part of the daemon, and the running application never imports
or invokes it. It exists only between the user double-clicking a downloaded zip
and the supported Linux daemon starting; from that point on the app is the
ordinary Linux install described in
[`../../docs/release-validation.md`](../../docs/release-validation.md).
