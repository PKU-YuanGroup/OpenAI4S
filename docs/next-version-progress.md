# OpenAI4S v0.3 — implementation progress and completion evidence

> Plan: `OpenAI4S-next-version-integrated-report-20260725.md`
> Decisions: [`v03-decisions.md`](v03-decisions.md)
> Baseline: `next` @ `126ef91`
>
> A per-item factual record, not a plan. Every status must be supported by
> **a real main-path wiring plus an executable verification command**.
> The existence of a class, function or file with the right name is **not**
> completion evidence.

## Status vocabulary

| Status | Meaning |
|---|---|
| `Completed` | Wired into the real main path, acceptance conditions met, regression test present |
| `Partially completed` | Partly wired; the remainder is named explicitly |
| `Implemented but unverified` | Code exists and looks correct, but the run that would prove it cannot be performed from a working copy — the missing run is named |
| `Not started` | Not begun |
| `Deviated` | Implemented differently from the plan; the reason is stated |
| `Blocked` | Blocked on an external decision, credential or machine |
| `Obsolete` | No longer applicable under the current design |

## How each item below was verified

Unless a row says otherwise, `Completed` means all of:

1. `uv run pytest` (whole offline suite) green;
2. `uv run mypy` green;
3. `uv run pre-commit run --all-files` green;
4. `uv run python scripts/capture_response_contract.py --check` green;
5. **the new test was verified to fail when the defect is put back.** A test
   that cannot fail measures nothing, so each row names what was neutered.

---

## 1. Audit-added defects

Seven defects the integrated report does not mention, found by a read-only
audit of `126ef91` and confirmed by reproduction before any fix.

| # | Defect | Commit | Status | Falsification |
|---|---|---|---|---|
| A1 | Every UI edit of a Specialist silently NULLed both allowlists and reset `unrestricted` to true — a restriction that loosened itself | `33e649c` | `Completed` | Routing the partial update back through `upsert` fails both repository tests |
| A2 | Web-Customize skill edit rewrote frontmatter to `name/description/origin`, destroying `requirements`/`license`/`category` | — | `Not started` | Scheduled with P1-B, before the `requirements` parser |
| A3 | Cross-project memory leak: `list_memories(project_id=st.project_id or "all")` with `"all"` meaning no WHERE clause | `ae53e8e` | `Completed` | Restoring `or "all"` fails the forced-degenerate-state test |
| A4 | Imported sessions could restore no artifact version — snapshots written to a directory absent from `trusted_snapshot_dirs` | `9deac73` | `Completed` | Removing `session-imports` from the shared roots fails the test |
| A5 | `host.view_image` read any absolute path; an existence oracle for the host | `677f3f0` | `Completed` | Removing the confinement fails the test |
| A6 | `server/daemon.py`: a second HTTP server, `POST /run` → `Agent.run`, no Host allowlist, Origin check, token or headers | `b74372f` | `Completed` | A probe module defining a bare handler is reported by file and class |
| A7 | Kernel worker outbound frames had no size cap; one `print` allocated ~20 MB on both sides | `92501a4` | `Completed` | With both bounds removed the test fails on `20000000 <= 64064` |

## 2. P0-4 — error and state truth

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| 4.1 | Error envelope extracted to one definition in `errors.py`; the test module drives the real `Handler` instead of a copy of it | `83ea03b` | `Completed` | Neutering `public_failure` fails three tests; before the change the same neutering failed none |
| 4.2 | Both capture points observe the enriched body; artifacts regenerated | `e3bd0a4` | `Completed` | `grep -c request_id docs/response-*.json` went 0 → 1107 |
| 4.4 | `PLAN_STATUSES` enforced in the repository; `paused` added; `_spawn_job` distinguishes cancel from failure; startup reconciles orphaned `executing` rows | `23fab8c` | `Completed` | Removing the enum check and the reconciliation fails two tests |
| 4.3 | request-id propagation across job threads (`contextvars` do not cross `threading.Thread`) | — | `Not started` | — |
| 4.5 | Plan resume running only unfinished steps, reusing `ExecutionOwner` | — | `Not started` | Backend can hold `paused`; no resume entry point yet |
| 4.6 | Structured `ApiError` in the frontend; `paused` plan controls | — | `Not started` | — |

## 3. P0-3 — bounded runtime and transport

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| 3.1 | Process-group stop ladder extracted and shared by local jobs and `host.bash` | `2edd779` | `Completed` | — |
| 3.3 | Worker output bounded at the producer; one truncation marker; `_cap` counts what it says | `92501a4` | `Completed` | See A7 |
| 3.4 | Background cell peek buffer bounded | `1881eed` | `Completed` | Removing the cap fails the test |
| 3.5 | Local job log reports its own truncation; pruning no longer promotes a running job to newest | `3e30c3e` | `Completed` | Both defects restored, both tests fail |
| 3.6 | `host.bash` drains concurrently and times out by process group | `2edd779` | `Completed` | Killing only the shell fails the real-subprocess test |
| 3.7 | MCP: single reader, id-keyed demux, abandoned-id set, absolute deadline, bounded frames, stderr tail, reaping close, registered probes, eviction on edit/disable | `34c8f8c` | `Completed` | Without the deadline the silent-connector test blocks past 30 s |
| 3.8 | `glob`'s `count` means what it returns; `grep`'s `include` recurses | `dba11ac` | `Completed` | Both defects restored, both tests fail |
| 3.9 | Per-session execution queue depth cap | `6cd5f73` | `Completed` | Removing the check fails the test |
| — | `WorkspaceFileService.workspace()` memoised: 16.5 µs → 0.1 µs per call | `839d4e6` | `Completed` | Removing the memo fails the syscall-count test |

## 4. P0-2 — identity and scope closure

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| 2.x | Compute event stream owner-scoped; ssh alias cannot be read as an option | `c7b2092` | `Completed` | `ssh:-oProxyCommand=touch /tmp/pwned` refused; both checks removed, both tests fail |
| 2.x | `sqlite_master` denied to `host.query` (it returned the full DDL of denied tables) | `dea321c` | `Completed` | Removing it from the denylist fails the test |
| 2.x | A filename that names two artifacts names none | `a70c50f` | `Completed` | Relaxing to first-match fails the test |
| 2.x | `default_model_id` no longer drifts to a provider default on restart | `d9c9610` | `Completed` | Restoring the process-config seed fails the test |
| 2.5 | Upload refuses non-base64 instead of storing the text; three content fields mutually exclusive | `a4b592d` | `Completed` | Dropping `validate=True` fails the corruption test |
| 2.x | Lineage walk bounded by default and reports truncation; `skills/` egress surface frozen | `7a21459` | `Completed` | A planted fourth networked sidecar is named by the gate |
| 2.4 | Version-keyed reads (`lineage_get`, `artifact_path`, `lineage_graph`) confined to the calling session | `3301579` | `Completed` | Removing the scoping fails the test |
| 2.6 | Same-project cross-session materialisation (D3) | — | `Not started` | Needs a third atomic repository write |
| 2.10 | `ModelSelection` immutable revision, `frames` migration, 409 rebind (D2) | — | `Not started` | — |
| 2.17 | The three networked skill sidecars migrated to the Host network capability | — | `Partially completed` | Surface frozen (`7a21459`); migration not done |

## 5. P0-1 — no implicit startup, local authentication

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| 1.3 | Dead unauthenticated second HTTP server deleted; guard against a replacement | `b74372f` | `Completed` | See A6 |
| 1.1/1.2 | Demo seed opt-in; no implicit kernel, network, cell or artifact on first boot | — | `Not started` | — |
| 1.4 | Local auth required by default (D1) | — | `Not started` | — |

## 6. P0-0 — exact-source-SHA release evidence

| # | Item | Commit | Status | Falsification / missing run |
|---|---|---|---|---|
| 0.x | `step_test`'s false "the suite gated the build" replaced by a receipt bound to the released SHA; three refusal paths tested | `5e32495` | `Completed` | Neutering the receipt check fails four tests |
| 0.x | Quality job, receipt upload, release concurrency mutex, timeouts on all seven jobs, `attach` runs inside the checkout | `5e32495` | `Implemented but unverified` | **Missing run:** one real `workflow_dispatch`. Nothing in this repository executes `.github/workflows/*.yml`. |
| 0.x | Every ci.yml action pinned to a digest; `inputs.tag` no longer inlined into `run:`; `persist-credentials: false` on the write-capable checkouts | `2eb3544` | `Implemented but unverified` | Each digest was independently re-resolved and matched. **Missing run:** one real CI run. |
| 0.x | `docs/release-validation.md` corrected in three load-bearing places | `5e32495` | `Completed` | — |
| 0.4 | Release evidence bundle sealed for `evidence.verify_package` | — | `Not started` | — |
| 0.5 | Python support matrix reconciled (classifiers 3.10–3.12 / CI 3.10+3.12 / DMG 3.13) | — | `Not started` | — |
| 0.7 | macOS signing/notarization state vocabulary (D11) | — | `Not started` | `Blocked` for the certificate itself |

## 7. Cross-cutting engineering

| Item | Commit | Status |
|---|---|---|
| Route-module inventory derived from the filesystem, with a convention guard | `3f4f59b` | `Completed` |

## 8. Not started

`P1-A` (visible product closure), `P1-B` (Agent/Skill/Compute control planes),
and all of `P2` (design freeze and real-platform experiments). P2 by decision
D8 enters no public API, schema or definition of done in this version.

## 9. Externally unverifiable

See [`v03-decisions.md`](v03-decisions.md#externally-unverifiable). Nothing in
this file marks those `Completed`.
