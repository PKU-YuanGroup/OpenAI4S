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
| 4.3 | request-id carried into the turn/plan/REPL/local-job threads by an explicit context copy; `MessageJob` records the id it was built under so a failed job's result and its log line share one; daemon-lifetime sweepers deliberately excluded | `8a20ae6` | `Completed` | Unwiring any one spawn site fails the wiring test *by thread name*; the behavioural half asserts a bare thread still sees `""`, so the helper cannot be moot |
| 4.5 | Plan resume: `POST /frames/{id}/plan/resume` runs only the unfinished steps, through the same FIFO-owned turn the approve path uses; refused per-status with its own reason; a paused plan with nothing left completes instead of running an empty turn | `8a20ae6` | `Completed` | Three mutations caught: treating `failed` as unfinished, treating `in_progress` as settled, and relaxing the paused-only guard |
| 4.6 | `ApiError` keeps the whole envelope (`code`/`status`/`request_id`); 53 user-facing hints now show the id; four hand-rolled lossy conversions removed; the dead `/404/.test(e.message)` branch reads the structured status; `paused` plans render and offer a resume control | `8a20ae6` | `Completed` | Each of the three gates fails on its own reinstated defect |
| 4.7 | Customize skill failures reach `public_failure`: every soft dictionary carries a stable `code`, and the gateway projects it to a status (**five** routes, not the six first recorded — `set_enabled` returns `{"ok": True}` unconditionally and never fails). The four sibling routes that already answered 4xx keep their statuses and gain the specific code | `585aaf4` | `Completed` | Forcing the gateway back to 200 fails three tests; removing one code from the status table names it. The test that asserted `(200, {"error": …})` and was *named* "keep soft errors" was rewritten, not deleted |

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
| 2.17 | All three networked skill sidecars migrated off raw `urllib`; the Host network capability grew the three things that were missing — `web_fetch(method="HEAD")` (one hop, no redirect following, so doi.org's own 302/404 survives), `user_agent=`, and `web_download` (workspace-confined, byte-capped while reading). `_SKILL_EGRESS` is now **empty** | *pending* | `Completed` | Replanting one `urlopen` in a skill is reported by file and line; removing the path check, the byte cap or the HEAD guard each fails its own test |

## 5. P0-1 — no implicit startup, local authentication

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| 1.3 | Dead unauthenticated second HTTP server deleted; guard against a replacement | `b74372f` | `Completed` | See A6 |
| 1.4 | Local auth required on loopback by default (D1): persistent owner-only token minted atomically, CLI credential + `OPENAI4S_TOKEN` escape hatch, constant-time compare, mutation query token refused, `/auth/status` reports the real mode, `OPENAI4S_REQUIRE_TOKEN=0` loopback-only for one minor release | `57d4ff7` | `Completed` | Restoring the opt-in default fails the default test; the DNS-rebinding test was deliberately made *authenticated* so it still proves the Host check rather than the gate |
| 1.1/1.2 | Demo seed opt-in; the example moved behind `POST /example/session` with `{"confirm": true}` and a dashboard button | `57d4ff7` | `Completed` | Restoring the `"1"` default fails the behavioural test with all six cells listed — not just the flag test |
| 1.x | The browser client's 3Dmol CDN fallback removed; frontend egress surface frozen | `57d4ff7` | `Completed` | Replanting the fallback fails both new gates by file and line |

## 6. P0-0 — exact-source-SHA release evidence

| # | Item | Commit | Status | Falsification / missing run |
|---|---|---|---|---|
| 0.x | `step_test`'s false "the suite gated the build" replaced by a receipt bound to the released SHA; three refusal paths tested | `5e32495` | `Completed` | Neutering the receipt check fails four tests |
| 0.x | Quality job, receipt upload, release concurrency mutex, timeouts on all seven jobs, `attach` runs inside the checkout | `5e32495` | `Implemented but unverified` | **Missing run:** one real `workflow_dispatch`. Nothing in this repository executes `.github/workflows/*.yml`. |
| 0.x | Every ci.yml action pinned to a digest; `inputs.tag` no longer inlined into `run:`; `persist-credentials: false` on the write-capable checkouts | `2eb3544` | `Implemented but unverified` | Each digest was independently re-resolved and matched. **Missing run:** one real CI run. |
| 0.x | `docs/release-validation.md` corrected in three load-bearing places | `5e32495` | `Completed` | — |
| 0.4 | Release evidence bundle sealed for `evidence.verify_package` | — | `Not started` | — |
| 0.5 | Python support matrix reconciled: 3.13 classified and added to the CI matrix, and the three files are compared by a test rather than restated in prose | `20b46cd` | `Completed` | Reverting the classifier, the CI matrix or the `requires-python` floor fails a different arm each time, naming the exact file conflict |
| 0.7 | macOS signing/notarization state vocabulary (D11) | — | `Not started` | `Blocked` for the certificate itself |

| 4.x | **Found by running it, not by reading it:** the startup access-token banner used block-buffered stdout while every neighbouring notice uses stderr, so under `nohup`/systemd/Docker the credential a user needs to open their own daemon never appeared | `8a20ae6` | `Completed` | Reproduced against a real daemon with stdout redirected to a file: banner absent before, present after |
| 4.x | Both browser harnesses navigated to `/` with no credential and would have failed every check on a 401 once the gate was on; a shared `tests/browser_auth.mjs` logs in through the `?token=` bootstrap, exercising the 303 and cookie hand-off | `8a20ae6` | `Completed` | Neutering the login makes `browser_smoke.mjs` fail at its first check with HTTP 401 |
| 4.x | `PlanRepository.create` did not enforce `PLAN_STATUSES` while `update` did, and session import fed it a status straight from an uploaded package; an imported plan claiming `executing` now arrives `paused` | `8a20ae6` | `Completed` | Disabling either the enum check or the `executing`→`paused` mapping fails its own test |

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
