# v0.3 frozen decisions

Owner-signed answers to the open decisions in the next-version integrated
report (`OpenAI4S-next-version-integrated-report-20260725.md`, §12), plus the
acceptance criterion that decides when this version is done.

This file exists for the reason [`v02-decisions.md`](v02-decisions.md) does:
work that depends on one of these answers must not start until the answer is
recorded somewhere a reviewer can check it. Each row states what was chosen
and, more usefully, what the choice **forecloses** — a decision whose cost is
invisible gets silently reversed later.

## Superseded from v0.2

| # | v0.2 said | v0.3 says | Why |
| --- | --- | --- | --- |
| PR granularity | One large PR per phase | **Small PRs, decomposed by contract** | v0.2 accepted review burden in exchange for gate-at-once verification. This version's work is a set of independent boundary fixes rather than one phase, and several of them touch the same transaction-sensitive code (`record_cell_artifact`, `_split`, `_json`) where a combined diff is unreviewable. Each change lands with its own regression test and its own revert point. |

## Frozen 2026-07-26

| # | Decision | Choice | Consequence |
| --- | --- | --- | --- |
| D1 | loopback authentication | Required by default; persistent owner-only token; **exactly one** minor release of an explicit legacy loopback mode | The mutation `?token=` path is banned permanently and does not come back with the legacy mode. Non-loopback binds stay `required` unconditionally. Turning the gate on breaks every CLI subcommand until the CLI sends a credential, so those land together or not at all. |
| D2 | Model Profile identity | Full immutable revision. A session binds `profile_id + revision`; editing mints a new revision; a missing or unusable one returns `409` and asks for a rebind | No silent "follow latest". A replayed session can say which configuration it actually used. Legacy sessions auto-backfill only on a unique `(provider, endpoint, model)` match — an ambiguous one stays unbound rather than guessing. Costs a `frames` migration. |
| D3 | same-project cross-session Artifacts | Code that needs the file **materialises** it into the target session as a new Artifact/version, with source→target lineage | No path that reads another session's file in place. Cross-project stays refused, and refused without disclosing whether the object exists. Requires a third atomic repository write beside `record_cell_artifact` and `record_artifact_restore`. |
| D4 | Plan `paused` / resume | P0 must include both, plus a one-time reconciliation of rows already stuck on `executing` | A stuck plan permanently shadowed every new draft for its session, because `get_by_frame` prefers the newest non-discarded plan. Shipping the state without the reconciliation would leave existing installs stuck forever. |
| D5 | Specialist allowlists | P0 fixes the partial-update data loss and **hides** the resource-restriction UI; tri-state semantics and Host-RPC enforcement land in P1-B | No lock is displayed that is not enforced on the direct `host.delegate` path. Hiding the UI does not fix the wipe, so the backend fix is the one that had to be immediate. |
| D6 | channel budgets | Initial values with env-var overrides now; frozen after real-subprocess stress tests | Budgets may be raised. The total bound may not be removed, and no path returns to an unbounded read. |
| D7 | audit-discovered defects | Folded into their natural batch, marked "audit-added" in the ledger | No separate batch, so the scope-closure work touches each area once instead of twice. |
| D8 | version scope | P0 + P1 + P2 planned; **P2 enters no public API, schema, or definition of done** | P2 is design-freeze and real-platform experiments only. |
| D10 | progress ledger | New `docs/next-version-progress.md`, **tracked in git** | The DoD and every "implemented but unverified" marking are reviewable in a diff. Costs keeping `docs/README.md` and `docs/README_zh.md` row counts in step, which `check_directory_readmes.py` compares. |
| D11 | macOS DMG release gate | **Not decided.** P0-0 implements the `verified / preview / not_notarized / not_configured` state vocabulary and the evidence-bundle fields, and leaves `release_pipeline.py`'s hard failure alone | No policy loosening. The consequence is that the macOS asset has **no publishable path** in this version, and that must be said plainly in the evidence bundle and the docs rather than left looking untested. |
| D12 | acceptance criterion | "**All offline gates green**", with every item marked `Completed` or `Implemented but unverified` and the missing real run named | Three categories cannot be verified from a working copy: nothing in this repository executes `.github/workflows/*.yml`; the auth flip's blast radius is the CLI and a live-daemon browser smoke; P2 is blocked on external credentials. Marking those `Completed` would be the same class of claim as `release_pipeline.py`'s "the suite gated the build", which this version deleted. |

## Budgets — initial values, not yet frozen

Set now so the mechanism exists; to be frozen here after stress testing under
real subprocesses. Every one is enforced **at read or allocation time**, never
by truncating after an unbounded read, and every response reports what was
seen, retained and dropped.

| Channel | Initial | Override |
| --- | --- | --- |
| MCP request deadline | 60 s absolute | `OPENAI4S_MCP_DEADLINE_S`, clamped to 1–600 s; anything unparseable, non-finite or out of range falls back to 60 s |
| MCP frame | 4 MiB | — |
| MCP stderr tail | 200 lines | — |
| Kernel outbound frame | 8 MiB | — |
| Kernel streamed chunk | 64k chars | — |
| Cell output retained | 1M chars | — |
| Background cell peek | 1M chars | — |
| Local job log | 200k chars (tail) | — |
| `host.bash` stdout / stderr | 30k / 8k chars (tail) | — |
| Execution queue depth | 64 per session | — |
| Lineage walk | depth 32, 500 nodes, 5000 edges | — |
| Glob matches | 1000 | — |

## Externally unverifiable

Recorded here so their absence from the "verified" column is deliberate rather
than an oversight. None of these can be established from a working copy.

| Item | What is missing |
| --- | --- |
| GitHub Actions execution | Nothing in this repository runs `.github/workflows/*.yml`. The release quality job, the receipt upload, the concurrency mutex and the job timeouts need one real `workflow_dispatch`. |
| Developer ID certificate | `build_macos_dmg.sh` only ad-hoc signs, so `--mode release` cannot pass for a DMG. |
| Apple notarization | Requires the paid identity above. Reported as a state, never as verified. |
| PyPI OIDC publish | Only exercised by a real release. |
| ~~Live browser smoke and the cross-engine matrix~~ | **No longer unverifiable — both were run, in all three engines.** Both harnesses were driven against a real daemon on `127.0.0.1:8760` with CI's environment, and both pass. A later run in all three engines caught a third: `browser_matrix.mjs` navigated to the app *twice* — `authenticate` already performs a real top-level navigation and lands on `/` after the 303 — and the second navigation aborted the in-flight startup fetches of the page it replaced. Chromium and Firefox drop an aborted fetch quietly; WebKit surfaces it as a page-level error, so the file failed its own "no uncaught page errors" check on one engine out of three, for a request the app had handled and an abort the harness had caused. Running them is what caught two things the offline suite could not: the auth flip 401'd every check in both harnesses (they navigated to `/` with no credential — fixed by `tests/browser_auth.mjs`, and verified to fail again when the login is removed), and the startup token banner went to block-buffered stdout, so the one line a user needs to open their own daemon never appeared under `nohup`/systemd/Docker. |
| bubblewrap private-PID runtime | Team Cells now use `--unshare-pid`; otherwise `/proc/<sibling>/root` aliases bypass the filesystem mounts. Bubblewrap's inherited `--info-fd` JSON is read under a five-second/4096-byte bound, but its `child-pid` is the raw-clone namespace init—not the later Python/R command. The Host therefore pidfd-pins that init as an authenticated anchor, verifies it is the outer launcher's sole direct child, waits at most five seconds for exactly one init child, pidfd-pins that command, and revalidates the complete launcher → init → command chain plus both live pidfds before retaining only the command pidfd. Zero/multiple children, parent changes, missing/invalid reports, hidden procfs and kernels without pidfd support all fail worker launch; restart/close discard the old channel and pidfd. Interrupts use only `pidfd_send_signal`. Forced-Linux unit tests cover argv ordering, channel/procfs bounds, zero/multiple children, parent changes, launcher mismatch, signal targeting and cleanup. The dedicated `linux-bwrap-kernel-interrupt` CI job supplies the missing runtime evidence with real persistent Python and R workers: it asserts `--unshare-pid`/`--info-fd`, PID 2 under the reaper, a pinned command pidfd, delivered SIGINT, unchanged generation and a succeeding follow-up Cell. It explicitly allows raw worker networking to fit GitHub's hosted runner, so that result proves process identity and interrupt persistence—not the broader Linux network boundary. Single-user mode retains the host PID namespace and its daemon-environ mask for compatibility. |
| Linux CI behaviour | Development is macOS: `sh` execs where macOS forks, there is no Seatbelt but there is bubblewrap, and the platform branches taken there are not taken here. |
