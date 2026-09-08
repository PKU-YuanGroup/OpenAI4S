# Windows / WSL2 parity investigation

[中文说明](windows-wsl-parity-audit_zh.md)

Baseline: `bca1183fcc1cd9f4820bb54139ecdaaf63adfcf6`, 2026-09-07.
The user already has WSL2. The target is the same scientific workflow as the macOS download.
The branch now includes production fixes following the original investigation. The baseline observations below are historical; they are not the current branch's results. This is WSL2 x86_64 validation, not certification of every Windows configuration.

## Implemented fixes

| Findings | Change | User-visible result |
| --- | --- | --- |
| W0 | Focused WSL adapter, Windows/session mount masks, private PID namespace and AF_VSOCK filter; reuse the existing sandbox and interrupt lifecycle | Scientific code cannot use Windows interop to bypass its boundary; Python/R cancellation preserves the live analysis state |
| W1, W5 | Exclude infrastructure distributions; verify architecture, ordinary user and usable bubblewrap; offer Ubuntu prerequisite installation | First launch selects a user distribution and explains/prepares the missing isolation component |
| W2 | Windows user-scoped DPAPI backend behind SecretBroker; retain an existing usable Linux keyring | Fresh WSL can save model credentials without setting up a Linux desktop keyring or writing plaintext |
| W3, W4, W9 | Embedded runtime on CLI PATH, Linux tool lookup, explicit Bash executable | Shell commands use the selected analysis environment and support `source` / `[[ ]]` |
| W6–W8 | Digest-addressed staging and atomic current pointer, package-independent management, running-build identity | A failed update preserves the working installation; updates report a pending restart and leave active work running |
| C1, C2 | Shared macOS/Linux builder fixes for default UI and relocatable Python entry points; stronger artifact verification | The downloaded app opens its actual workbench and scientific commands keep working after installation |
| W10 — found during browser acceptance | One process-creation thread lives with the daemon instead of a short request | A completed Web Cell no longer loses its worker when its request thread ends; subsequent Cells and reloads retain variables |
| W11 — found during standalone launch | The Windows launcher retains a hidden WSL process for the foreground Linux server | No terminal needs to stay open; stopping the server also releases the background WSL process |
| C3 — found in installed-package acceptance | Bundled Linux/macOS CLIs disable Python's implicit current-directory import with `-P` | A project containing `openai4s.py` or a source checkout cannot replace the installed application; the working directory is preserved |

The final full-suite check also reproduced an existing HTTP timeout race. The shared bounded reader now classifies a socket timeout consistently even when it precedes the watchdog. Regression tests exercise both orderings without loading unrelated TLS trust files into a 200 ms socketpair deadline.

The WSL-specific policy lives in `security/wsl.py`; encrypted storage lives in `security/windows_dpapi.py`. The existing kernel protocol, scientific services and UI remain unchanged. The two short-lived sandbox callers now pass the same required descriptors as persistent kernels. Hardlink scanning is retained for team isolation and Windows-backed workspaces, rather than scanning every Linux development checkout at startup.

Automated acceptance includes `harness.smoke.wsl_sandbox`, the real Python/R interrupt smoke, offline regression contracts, native PowerShell 5.1 launcher contracts and actual Windows-browser/package checks. See the follow-up verification record at the end of this report.

## Baseline conclusion and evidence

The existing Linux Python kernel runs in WSL. Most gaps are missing product setup or additional boundaries between Windows and Linux. macOS already gets an embedded Python/science stack, launcher environment, OS sandbox and login keychain. Having WSL2 alone does not provide equivalent conditions inside a distribution.

The first release blocker is **Windows interoperability escaping the Cell sandbox**. After the existing full Linux boundary smoke passed, an actual enforced Python Cell launched Windows PowerShell and changed a synthetic marker outside its workspace. The Linux smoke does not establish the WSL boundary.

- Testing used an isolated `OpenAI4S-Parity-Test` distribution, an ordinary user, official Ubuntu 24.04.4 x86_64 and WSL2 kernel `6.18.33.2-microsoft-standard-WSL2`.
- Initially bubblewrap, secret-tool, Rscript, Conda and Linux Node were absent. Bubblewrap 0.9.0 and development tools were installed only in this distribution, followed by libsecret-tools, gnome-keyring and dbus-user-session.
- Probes used temporary directories and synthetic data. No real credentials or provider calls were used; Docker's distribution was untouched.
- macOS comparisons are from current source, not an executed DMG. One WSL distribution cannot certify other distributions, architectures or networking modes.
- The [probe](../scripts/probe_wsl_parity.py) and [receipt](windows-wsl-parity-evidence.json) retain the observations. Temporary paths in the receipt describe this run, not product defaults. The CLI environment fixture uses a stub interpreter and establishes PATH routing only.

The official WSL image SHA-256 was `9b2f7730dc68227dd04a9f3e5eab86ad85caf556b8606ad94f1f29ff5c4fd3f5`, verified against [Ubuntu's checksum list](https://releases.ubuntu.com/24.04/SHA256SUMS).

The complete payload was also built: approximately 406 MiB compressed and 1.4 GiB installed. Its existing verifier successfully imported 38 scientific packages. After extracting the actual Windows ZIP under a path containing spaces and Chinese characters, the PowerShell 5.1 launcher installed as the ordinary user, started the daemon, reported ready and exited zero. Windows HTTP requests returned 401 without authentication and **404 for the authenticated homepage**; the installed default UI is absent. Connectivity/authentication work, but artifact verification and the ready check miss the default page.

After keyring packages were installed, D-Bus ReadAlias(default) returned `/` and Collections contained only session. The broker's real store self-test timed out; a usable persistent default collection was not established.

The actual launcher status returned the running PID, reopening reused that daemon, stop exited zero, and subsequent status exited one with not running. The test daemon has been stopped.

## Top down: user workflow

Windows ZIP → distribution/user selection → prerequisites and Linux payload installation → Linux daemon → authenticated Windows browser → model credentials → upload and Python/R/Bash execution → artifacts/download → stop, reopen, upgrade and recovery.

| User action | Observation | Acceptance needed |
| --- | --- | --- |
| Extract and launch | Selector admits docker-desktop; fresh Ubuntu stops at missing bwrap | Exclude infrastructure distributions; check architecture, user and runtime compatibility; provide a usable prerequisite setup flow |
| Open UI | The real ZIP installs and the launcher reports ready, but the authenticated homepage returns 404; the Linux verifier passes | Verify installed default HTML and referenced JS/CSS; correct the shared macOS builder too |
| Configure a model | Fresh WSL has no available secure backend; installing keyring packages alone does not create one | Secure save/read, unlock, reopen and WSL-restart recovery; no implicit plaintext fallback |
| Analyze uploaded CSV | Real Python and persistent variables work; bundled CLI supplies the wrong shell PATH | Verify selected Python and shell tools, upload, dataframe/figure capture and Windows-browser download |
| Run Bash | POSIX command succeeds; `source` and `[[ … ]]` return 127 through real host.bash | One deliberate foreground/background shell contract, correct dependency environment, retained authorization and cancellation |
| Run R or specialist tools | Neither base desktop bundle includes R/Conda or every domain CLI | Capability-specific environment provisioning, real binaries, R fd transport and post-interrupt recovery |
| Isolate a Cell | Ordinary Linux boundary smoke passes; Windows marker can still be changed | Cover Windows processes/IPC, filesystem aliases, credentials and networking as part of WSL enforcement |
| Stop/reopen | 199 existing tests passed; the real ZIP's status, reuse, stop and post-stop status passed | Add conflicting ports, active-work cancellation, complete subsequent scientific execution and restart recovery |
| Upgrade/recover | Bad same-version archive removes previous CLI; management commands require/install the new payload; running version is not compared | Preserve a working install on failure, diagnose/stop without a good new ZIP, report real running version and protect active work |
| Restart WSL/Windows | Full product recovery not yet certified | Stable distribution/user selection, persistent credentials/data/environments and updated addressing |

Uploads, downloads and WebSockets already use HTTP. Validate these flows before inventing cross-filesystem mapping. Proxy, Fake-IP and localhost fallback implementations already exist, but multiple network modes were not tested by changing global machine settings.

## Confirmed findings

| ID | Evidence and impact | Entry point / required change |
| --- | --- | --- |
| W0 — release blocker | An enforced Python Cell executes Windows PowerShell and changes its own out-of-workspace marker even with the ordinary Linux smoke green | `kernel/manager.py` → `security/sandbox.py:wrap_bwrap_command`. Account for WSL executable/IPC/mount/proc boundaries; stripping WSL_INTEROP from the worker environment did not prevent this reproduction |
| W1 — first run | Fresh Ubuntu `bootstrap.sh preflight` exits 1 because bwrap is missing; it prints apt instructions | `scripts/windows/bootstrap.sh:run_preflight`. Provide prerequisite setup and actual enforced self-tests |
| W2 — credentials | SecretBroker fails closed without a system store; secret-tool alone does not imply a user session and unlocked Secret Service | `security/secret_broker.py`. Define ownership, service lifetime, unlock and restart behavior before implementing a backend; a Windows secure-store bridge also depends on W0 being closed |
| W3 — execution | The bundled CLI does not prepend runtime/bin. The fixture resolves python3 to `/usr/bin/python3` and cannot find python | Linux builder CLI → bootstrap serve → kernel environment. macOS desktop LAUNCHER sets PATH; both CLI wrappers lack it. Unify launch environment while keeping selected Conda environments authoritative |
| W4 — execution | `shell=True` uses Ubuntu `/bin/sh`; `source` and `[[ ]]` fail. Jobs explicitly uses Bash | `sdk/bash.py:BashExecutor`, `jobs.py`. Preserve authorization, timeout, process groups and audit when unifying the contract. R's POSIX fd redirection through sh is valid and need not be mechanically changed |
| W5 — distribution | The real selection function with a synthetic list chooses docker-desktop if it is the only WSL2 distribution; nothing was run inside Docker | `openai4s.ps1:Select-Distro`. Filter infrastructure, probe capability, preserve data ownership and explicit selection |
| W6 — upgrade | TCP plus CLI status reuses an existing daemon without comparing the running build to the newly installed build | PowerShell main flow and `Test-OpenAI4SServing`. Separate installed/running versions and active work; offer safe restart or pending-restart state |
| W7 — recovery | status/stop/doctor skip sandbox preflight but still call Get-PackageFacts → install → cli | PowerShell Arguments flow. Management of an existing installation must work without an intact new payload and without reinstalling to answer a query |
| W8 — recovery | A matching checksum over invalid archive bytes passes digest verification, tar fails, and the old same-version CLI is gone | `bootstrap.sh install` removes before extraction. Stage, verify and switch with concurrency/recovery rules. Different versions already have distinct directories; this does not prove session loss on every upgrade |
| W9 — tool discovery/latency | This WSL PATH had 43 entries, 34 under /mnt. Five missing-command lookups had a median of 209 ms versus 0.034 ms with Linux-only PATH. Windows npx was found without Linux node | Inherited CLI/daemon PATH and which callers such as `cli/main.py:_find_conda_tool`. Define Linux tool resolution, verify executability and invalidate caches on environment changes; do not change global WSL settings. This local measurement is not a full attribution of suite runtime |
| C1 — shared release blocker | Real rsync omits `webui/dist/index.html`; shared source verifier still passes and counts 604 Skills | Both bundle builders exclude every `dist`; REQUIRED_SOURCES only checks the legacy UI. Include default UI and referenced assets, then verify all desktop artifacts, not only wheels |
| C2 — scientific CLI relocation | 22 console scripts in this actual payload embed the builder's interpreter path. Hiding the build directory only in a temporary mount namespace makes installed f2py fail, while the same installation's `python3 -m numpy.f2py -v` and pip succeed | Linux builder pip installation/runtime/bin; the macOS builder uses the same installation strategy and needs comparison. Relocate console scripts and execute them without builder paths visible; the current verifier misses these entry points |

The POSIX default of `shell=True` is documented by [Python subprocess](https://docs.python.org/3/library/subprocess.html#subprocess.Popen). These observations have concrete environment and lifecycle causes; they do not establish general incompatibility with Linux syscalls.

## Bottom up: shells and process boundaries

The [inventory](windows-wsl-shell-inventory.json) records all 226 tracked `.sh` / `.command` / `.ps1` / `.cmd` files at the baseline, six generated shell heredocs and 25 directly recognizable Python subprocess / host.bash call sites. Two remote `.sh.tmpl` templates are additional rendered-shell boundaries. All 224 `.sh` / `.command` files passed real Ubuntu `bash -n`. Syntax acceptance is not dependency or workflow certification. AST discovery cannot guarantee coverage of dynamic aliases, generated code or arbitrary configuration commands.

A targeted search of domain shell scripts found no brew, osascript, pbcopy/pbpaste, /Applications, /Users or /opt/homebrew matches. That rules out those specific indicators, not all possible workflow incompatibilities.

| Layer | Files / modules | Review boundary |
| --- | --- | --- |
| Build/distribution | Linux/macOS/Windows builders, notarization and tag scripts | Payload/architecture, UI resources, dependencies, permissions/symlinks and line endings; signing runs on macOS builders |
| Desktop entry | Three Windows entry files; CLI/LAUNCHER/INSTALL/UNINSTALL heredocs | Windows args → wsl.exe argv → Linux shell, identity, environment, versions and lifetime |
| Development entry | setup.sh, start.sh, setup_envs.sh | uv, venv and Conda prerequisites; a configured checkout is not a clean downloaded package |
| Execution | Kernel transport, R, SDK Bash, Jobs, dynamic tools, preinstall | Identity/environment, shell semantics, permissions, process groups, cancellation, streams and persistent state |
| Environments | CLI setup, env_generations, preinstall, environment definitions | Bundled base differs from named python/r; Python, pip and domain tools must belong to the selected environment |
| External programs | MCP, Ark CLI, git, scientific services | Execute under the actual Linux daemon environment. A discoverable Windows shim is not proof of a Linux tool: fresh WSL found Windows npx with no Linux node |
| Remote work | Compute/Slurm/local worker, fold_remote, run/wrapper templates | Remote Linux, SSH, GNU timeout/setsid and specialist dependencies; no real remote-server certification |
| Domain recipes | 213 scripts under skills | Delivered recipes do not imply installed binaries; provision capabilities such as STAR/samtools/mafft as needed |
| Supporting tools | Container smoke, benchmark, review scratch, BYOC confinement | Separate deployment/research capabilities, not implicit ordinary desktop startup dependencies |

R, Conda and the complete domain CLI stack are absent from the macOS base download too. The Stage 1 standard-profile gate is off by default; its requirement for named python/r environments when enabled does not establish failure of default Python execution.

## Validation record

The existing CI/release `windows-launcher` jobs cover PowerShell parsing and refusal on a machine without WSL, not successful installation on WSL2. The Linux runner's sandbox smoke also lacks Windows interoperability. A separate real WSL2 acceptance gate is needed after fixes; neither existing green check establishes it.

| Check | Result and limits |
| --- | --- |
| Existing kernel/Bash/platform/release tests | 199 passed as an ordinary WSL user |
| Shell syntax | 224 files passed; not all domain workflows executed |
| Full Linux boundary smoke | Enforced filesystem, network and child-secret checks passed; Windows channel W0 remains outside its coverage |
| Complete Linux payload verifier | Passed, executing embedded Python and importing 38 scientific packages; C1/C2 remain undetected |
| Actual Windows ZIP | Spaces/Chinese path installation, authentication, reopen and status/stop passed; default homepage returns 404 |
| All-file pre-commit / type checking | Passed |
| Bilingual directory coverage / source secret scan | 160 directories and 1476 direct files covered; 3787 files scanned successfully |
| Complete offline suite | Completed: 8303 passed, 136 skipped, 2 failed, 1 warning in 852.74 s. `uv run --locked pytest -n auto --maxprocesses=4 --dist loadfile`, explicit Linux PATH, private tmpfs temporary data and `tmp_path_retention_policy=failed` |
| Failed-test recheck | Both passed in 17.92 s using `uv run --locked pytest -n 0` and ordinary disk temporary directories; this does not turn the full-suite result into a pass |

Conditions matter: the first direct `.venv/bin/pytest` invocation lacked uv on PATH and produced one release-build-command assertion failure; interruption recorded another 5172 passed / 103 skipped. With the corrected PATH and uv run, that test passed individually. A subsequent disk-backed attempt was interrupted during I/O waits at 1951 passed / 38 skipped and is not counted as a complete pass. The final ordinary-user offline run uses private memory-backed temporary data, retaining failed-test data. Actual installation, credentials, sandbox and relocation probes used the real Windows/WSL filesystems.

The completed suite failed `tests/test_mcp_client.py::test_streamable_http_body_watchdog_interrupts_a_chunked_slow_drip` with a raw `TimeoutError` instead of the expected `MCPTimeout`, and `tests/test_skill_product_surface.py::test_team_member_cannot_reactivate_legacy_host_skill_poison_over_http` because the HTTP helper received no status line. Both passed in the separate recheck. Their causes remain unconfirmed; neither is attributed to WSL, tmpfs or this documentation/probe change. The warning comes from the test that intentionally simulates unsupported native win32 sandboxing.

## Implementation order and acceptance

1. Close the WSL boundary and define secure credential storage. Add genuine WSL tests for Windows execution/IPC, paths/aliases, secrets and network channels, plus persistent Python/R and interruption. Do not make global WSL configuration changes a prerequisite for passing an app sandbox test.
2. Correct shared artifacts and execution environments: default UI and resource validation, CLI PATH, Linux tool discovery, console-script relocation, foreground/background Bash. Test from the final extracted ZIP with builder paths hidden and without relying on the developer's Python/Node/Conda.
3. Complete Windows installation/recovery: distro capabilities, prerequisite preparation, staged installation, package-independent management, running-build detection and safe restart. Include spaces/non-ASCII paths, corrupt archives, duplicate launch and concurrency.
4. Run the scientific workflow and restart regression: real model configuration, CSV upload, Python/plots/download, R provisioning, representative domain tools, Stop/recovery, reopen, WSL restart and upgrade. MCP/Ark login are separate extension capabilities.

Unverified scope remains explicit: real macOS execution, Windows on ARM, multiple distributions/network modes, real provider authentication/inference, every science recipe, and complete recovery after WSL/Windows restart.

## Reproduction

Run as an ordinary WSL user in a clean checkout with Bash/rsync. The sandbox probe requires bubblewrap. Supply an existing Windows scratch directory; the script creates and cleans up only its own random subdirectory there.

```sh
python3 scripts/probe_wsl_parity.py \
  --windows-scratch /mnt/c/path/to/audit-temp \
  --output /tmp/wsl-parity.json
OPENAI4S_KERNEL_SANDBOX=enforce python3 -m harness.smoke.linux_sandbox
```

Exit zero from the first command means collection finished, not parity achieved: inspect `windows_marker_changed` and the other observations. The second command passes at this baseline while missing the Windows interoperability issue demonstrated by the first.

For an actual installed artifact, substitute its install path and the original build root below. The tmpfs hides the build root only in the child mount namespace; it does not remove or move the real directory. In this run f2py failed while the module and pip invocations succeeded. Another 21 commands had the same absolute-shebang pattern; they were not all executed individually.

```sh
PARITY_APP=/path/to/installed/OpenAI4S-0.2.0-linux-x86_64
PARITY_BUILD_ROOT=/path/to/checkout/.build
bwrap --unshare-net --ro-bind / / --tmpfs "$PARITY_BUILD_ROOT" \
  --dev /dev --proc /proc -- "$PARITY_APP/runtime/bin/f2py" -v
bwrap --unshare-net --ro-bind / / --tmpfs "$PARITY_BUILD_ROOT" \
  --dev /dev --proc /proc -- "$PARITY_APP/runtime/bin/python3" -m numpy.f2py -v
bwrap --unshare-net --ro-bind / / --tmpfs "$PARITY_BUILD_ROOT" \
  --dev /dev --proc /proc -- "$PARITY_APP/runtime/bin/pip" --version
```

Concurrent build/install/full-suite work also saw WSL service connection timeouts (`0x8007274c`). One launcher attempt reported an unreachable folder after the same spaces/Chinese path had installed successfully. The service timeout's root cause was not established, and this does not demonstrate Unicode path incompatibility. Separately test service timeout, retry and diagnostic behavior.

## Fix verification — 2026-09-07

These results supersede the baseline failures above. The environment remains an ordinary user in the isolated Ubuntu 24.04 WSL2 x86_64 distribution; the Windows browser is Chromium and the launcher runs under Windows PowerShell 5.1. The core still uses only the Python standard library.

- The real WSL smoke passes with enforced isolation: Windows executables, a copied executable/interop loader, host process aliases, interop sockets and Hyper-V sockets cannot bypass the boundary. Confined environment probes and dynamic tools execute successfully. Python and R both retain variables after interruption; R/jsonlite were installed as test prerequisites, not added to the base product.
- The actual Windows browser can authenticate, load the default page and its resources, save/delete a synthetic model credential through DPAPI, upload/download CSV byte for byte, compute a mean of 2.0, render/download a PNG and exact result table, refresh and continue with the same dataframe, interrupt and continue, and export the notebook. Notebook execution was explicitly enabled with `OPENAI4S_NOTEBOOK_REPL=1`; no live model provider was called.
- The final payload executes its embedded Python/science validation. The final Windows ZIP passes native verification, including payload digest, ASCII PowerShell, CRLF/LF contracts and resources. Wheel and sdist build and verification pass. All-file pre-commit, strict mypy, 160-directory bilingual coverage and the source credential scan pass; the offline harness passes 38/38 scenarios.
- The final ZIP launched from a Windows source-checkout working directory and passed the scientist flow above. Five Windows-only health checks over 120 seconds confirmed standalone operation after the launcher exited, without WSL commands or UNC reads keeping the distribution alive.
- Installing the final payload while the previous payload was running retained its PID, runtime identity and live dataframe; saved results remained byte-identical. After a normal stop and a restart of only the test WSL distribution, the final package reopened those results and re-read the input CSV to compute 6. A synthetic DPAPI secret survived the restart and was then deleted. This does not claim that in-memory variables survive a daemon restart.
- Management worked from a launcher folder containing no payload. Final-package normal stops returned 0. One earlier stop exceeded the default five-second wait and returned 2, then exited on its own and released the hidden WSL helpers; no forced stop was used.
- The broader existing browser smoke still exceeded its 20-second queue-admission wait without a concurrent build. A separate instrumented run observed normal handoff after 18.585 seconds, with kernel construction taking 0.447 seconds and scientific bootstrap 10.247 seconds; an earlier loaded run took 39.1 seconds. The unmodified full browser script is **not passed**, and cold-start performance parity remains unverified. WSL service connection timeouts were also observed under concurrent load.
- The concurrent full-suite run was terminated around 83% by the 4 GB test VM's OOM killer and is not counted as a completed pass. Removing that load exposed W11 during standalone launch: a detached Linux service did not prevent this systemd WSL distribution from stopping. [Microsoft documents this lifecycle distinction](https://learn.microsoft.com/en-us/windows/wsl/systemd). The Windows launcher now retains a hidden WSL process while the Linux server runs in the foreground; stopping the server ends that process without changing global WSL settings.
- The final independent full offline run completed with **8345 passed, 107 skipped, 0 failed**, and one expected unsupported-platform sandbox warning, in 1052.39 seconds. It used two workers and a private 1 GiB tmpfs for temporary test files; a 2 GiB swap file added inside the lab late in the run was disabled and removed afterwards. Focused lifetime/MCP checks passed 37 tests; packaging/WSL checks passed 113 tests. Windows PowerShell 5.1 launcher contracts also passed.

Archive digests and final-run receipts are recorded in the evidence JSON's `fix_verification` section. Build validation reused the previously assembled science runtime and repeated the builder's source-copy, CLI generation, bytecode compilation, full Linux verification and Windows packaging steps with the production changes.

This is WSL2 x86_64 acceptance evidence. It does not certify macOS execution, ARM, arbitrary distributions/network modes, real provider login/inference, Windows reboot, Conda provisioning or all domain recipes.
