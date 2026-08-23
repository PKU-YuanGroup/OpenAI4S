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
| A2 | Web-Customize skill edit rewrote frontmatter to `name/description/origin`, destroying `requirements`/`license`/`category` | this batch | `Completed` | Measured across the 34 bundled skills: `license`+`category` on 23, nested `metadata` on 17, `requirements` on 13, `fold_cue` on 1 — all deleted by fixing a typo. Now edits raw lines rather than rebuilding. Restoring the rebuild fails 2 tests; parse-and-re-emit and a plain line filter each fail one, so both tempting wrong fixes are excluded |
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
| 3.7 | MCP: single reader, id-keyed demux, abandoned-id set, absolute deadline, bounded frames, stderr tail, reaping close, registered probes, eviction on edit/disable | `34c8f8c` | `Completed` | Without the deadline the silent-connector test blocks past 30 s. **That was the only fault arm this column cited, and one of the six had no test at all:** deleting `_MAX_INVALID_IDS` and its branch turned nothing red, because the wrong-ID flood was never driven. It is now, together with its two negative arms — a handful of unmatched ids must *not* drop a connector, and neither must a backlog of late answers to abandoned ids, which is what a merely slow connector looks like. The kernel's own stderr flood (a different channel from the MCP one) is driven at 10 MiB against a worker that floods and keeps running, which is where an unbounded tail is a live leak rather than one allocation. |
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
| 2.6 | Same-project cross-session materialisation (D3): `host.materialise_artifact` gives the caller its own Artifact/version plus a source→target lineage edge, in one transaction. Bytes are copied into a private immutable snapshot so exact restore/edit can enforce a single-link identity. Cross-project refused with the *same* message as absent — at both layers, worded identically so the depth is not itself a leak | `f078abf` | `Completed` | Removing the project bound at either layer fails; source, materialised snapshot, and writable live file keep distinct inodes |
| 2.10 | `ModelSelection` immutable revision (D2): append-only `revisions[]` keyed on `(provider, base_url, model)` — **not** on name or key, because the credential ref is derived from `profile_id`; migration **10** adds `frames.model_profile_id/revision`; binding happens on send only, so an unbound legacy session stays readable; `409 model_revision_unavailable` for a dangling pin and `409 model_revision_ambiguous` for a legacy session matching more than one profile | `5e56325` | `Completed` | Four mutations caught: sealing on every edit, ignoring the pin, guessing on ambiguity, and forgetting the `SCHEMA_VERSION` bump |
| 2.17 | All three networked skill sidecars migrated off raw `urllib`; the Host network capability grew the three things that were missing — `web_fetch(method="HEAD")` (one hop, no redirect following, so doi.org's own 302/404 survives), `user_agent=`, and `web_download` (workspace-confined, byte-capped while reading). `_SKILL_EGRESS` is now **empty** | `0453bea` | `Completed` | Replanting one `urlopen` in a skill is reported by file and line; removing the path check, the byte cap or the HEAD guard each fails its own test |

## 5. P0-1 — no implicit startup, local authentication

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| 1.3 | Dead unauthenticated second HTTP server deleted; guard against a replacement | `b74372f` | `Completed` | See A6 |
| 1.4 | Local auth required on loopback by default (D1): persistent owner-only token minted atomically, CLI credential + `OPENAI4S_TOKEN` escape hatch, constant-time compare, mutation query token refused, `/auth/status` reports the real mode, `OPENAI4S_REQUIRE_TOKEN=0` loopback-only until `LEGACY_TOKEN_OPT_OUT_REMOVED_IN` | `57d4ff7` | `Completed` | Restoring the opt-in default fails the default test; the DNS-rebinding test was deliberately made *authenticated* so it still proves the Host check rather than the gate. **This row read `Completed` before its exit matrix existed.** Three legs were claimed and untested: an unauthenticated WebSocket upgrade (every `/api/v1/ws` test presented a credential, on a synthetic handler that could not tell a refusal from a live socket), an unauthenticated REST *matrix* that was one route, and "cookie across restart" asserted as token-file stability rather than a cookie replayed against the handler that replaced the issuer. `tests/test_auth_exit_matrix.py` drives all three over a real socket; removing the gate turns six of its assertions red and minting the token per boot turns the two restart legs red on their own. "One minor release" was prose in four places and is now a version constant a test can fail on. |
| 1.1/1.2 | Demo seed opt-in; the example moved behind `POST /example/session` with `{"confirm": true}` and a dashboard button | `57d4ff7` | `Completed` | Restoring the `"1"` default fails the behavioural test with all six cells listed — not just the flag test |
| 1.x | The browser client's 3Dmol CDN fallback removed; frontend egress surface frozen | `57d4ff7` | `Completed` | Replanting the fallback fails both new gates by file and line |

## 6. P0-0 — exact-source-SHA release evidence

| # | Item | Commit | Status | Falsification / missing run |
|---|---|---|---|---|
| 0.x | `step_test`'s false "the suite gated the build" replaced by a receipt bound to the released SHA; three refusal paths tested | `5e32495` | `Completed` | Neutering the receipt check fails four tests |
| 0.x | Quality job, receipt upload, release concurrency mutex, timeouts on all seven jobs, `attach` runs inside the checkout | `5e32495` | `Implemented but unverified` | **Missing run:** one real `workflow_dispatch`. Nothing in this repository executes `.github/workflows/*.yml`. |
| 0.x | Every ci.yml action pinned to a digest; `inputs.tag` no longer inlined into `run:`; `persist-credentials: false` on the write-capable checkouts | `2eb3544` | `Implemented but unverified` | Each digest was independently re-resolved and matched. **Missing run:** one real CI run. |
| 0.x | `docs/release-validation.md` corrected in three load-bearing places | `5e32495` | `Completed` | — |
| 0.4 | Every run seals `openai4s-<version>-evidence.zip` in the format the product's **own** `evidence.verify_package` reads — not a second implementation that could disagree. A stopped run seals too; sealing is best-effort so it cannot fail a good release | `5e56325` | `Completed` | Dropping the manifest self-hash, or leaving a file out of the manifest, each fails; tampering and an added payload are both detected |
| 0.5 | Python support matrix reconciled: 3.13 classified and added to the CI matrix, and the four files are compared by a test rather than restated in prose | `20b46cd` | `Completed` | Reverting the classifier, the CI matrix or the `requires-python` floor fails a different arm each time, naming the exact file conflict. **It was three files, not four:** the reconciliation read `build_macos_dmg.sh` and not `build_linux_bundle.sh`, so the *second* shipped interpreter — the Linux tarball's, and through it the Windows zip's, since that wraps the same payload — could drift away from the classifiers and the matrix in silence, on the two platforms where the deliverable is the interpreter. Bumping `build_linux_bundle.sh` to an unclassified series now fails two arms. |
| 0.7 | `verified / not_notarized / preview / not_configured`, computed from evidence and never from a configured secret. `verified` is **unreachable** today (ad-hoc signing, no notarization attempt), so the macOS asset has no publishable path this version — stated in `macos_publishable`, not left as an absence. Per D11 the release-mode hard failure is untouched | `5e56325` | `Completed` | Inferring the state from `OPENAI4S_MACOS_SIGNING_IDENTITY` fails; claiming notarization fails the unreachability test, forcing the claim to be revisited rather than silently outdated |

| 4.x | **Found by running it, not by reading it:** the startup access-token banner used block-buffered stdout while every neighbouring notice uses stderr, so under `nohup`/systemd/Docker the credential a user needs to open their own daemon never appeared | `8a20ae6` | `Completed` | Reproduced against a real daemon with stdout redirected to a file: banner absent before, present after |
| 4.x | Both browser harnesses navigated to `/` with no credential and would have failed every check on a 401 once the gate was on; a shared `tests/browser_auth.mjs` logs in through the `?token=` bootstrap, exercising the 303 and cookie hand-off | `8a20ae6` | `Completed` | Neutering the login makes `browser_smoke.mjs` fail at its first check with HTTP 401 |
| 4.x | `PlanRepository.create` did not enforce `PLAN_STATUSES` while `update` did, and session import fed it a status straight from an uploaded package; an imported plan claiming `executing` now arrives `paused` | `8a20ae6` | `Completed` | Disabling either the enum check or the `executing`→`paused` mapping fails its own test |

## 7. Cross-cutting engineering

| Item | Commit | Status |
|---|---|---|
| Route-module inventory derived from the filesystem, with a convention guard | `3f4f59b` | `Completed` |

## 8. P0 is **not** closed — corrected 2026-07-29

This section read "P0 is closed. Every P0 item in the integrated report is
`Completed`" until a read-only production call-chain audit of `120af6a` went
through fourteen areas and came back with **no area fully wired**. The heading
was the most load-bearing claim in this file and it was false.

What the audit found, and where it now lives: every one of the 56 proposals has a
row in [`plan-crosswalk.json`](plan-crosswalk.json) with a status from a declared
vocabulary, checked by `tests/test_plan_crosswalk.py`. That file, not this
section, is the per-item record from here on — a table in prose can gain a
duplicate, lose a row, or say `Completed` about something no call chain reaches,
which is what happened.

**Re-audited at `408098f`.** The count that used to sit in this paragraph — "47
rows are `open`" — was itself a stale claim by the time anyone read it, which is
the failure this section is about, repeated. The re-audit went row by row against
the production call chain rather than against the previous label, and found the
labels lagging the work in both directions: the delegation stop/steer controls,
the owner-scoped remote task centre, the retrieval-source panel, the Notebook
language selector and the specialist connector allowlist were all `open` while
being implemented, wired in `app.js`, and covered by a named test. The local-job
deadline and abandoned receipt, the memory edit and its expiry quota, the plan
approve claim, the MCP byte budget and the checkpoint annotation binding were
genuinely missing and are now closed. The distribution at `408098f` is **48
`closed`, 5 `implemented_unverified`, 3 `deferred_p2`, and no `open` row**.

The five `implemented_unverified` rows are all the same missing run: one real
`workflow_dispatch` of the release workflow, plus notarization with credentials
this working copy does not hold. They stay unverified rather than closed on
purpose — a gate whose YAML has never executed is not a gate that passed.

Two examples of the shape of the error, because it recurs and is worth
recognising:

- **§6 recorded the quality receipt as `Completed`.** The receipt existed and was
  bound to the released SHA, which is what was checked. What nobody checked is
  that the consumer read only `format`, `source_sha` and a list of exit codes —
  never the gate names, the commands or the count. A two-row document naming
  `pytest` with the argv `["pytest"]` staged a release, and that document is
  verbatim what this repository's own test fixture wrote. The suite demonstrated
  the hole rather than closing it.
- **§6/0.7 stated that `verified` is "unreachable today" so the macOS asset has
  "no publishable path this version".** The stated reason was that the pipeline
  hardcoded `"notarized": None` — a fact about the pipeline, not about the image.
  Meanwhile the gate passed on the Developer ID signature alone, so the moment
  the signing-certificate secret exists (the workflow already imports it into a
  keychain) an un-notarized image publishes. "Unreachable" was documenting the
  absence of a check as if it were the absence of a capability.

The one shared reason that remains genuinely external is unchanged: **nothing in
this repository executes `.github/workflows/*.yml`**. Every workflow-level change
— the `freeze` job, the frozen-SHA checkouts, the build receipts, the platform
checks, the evidence and attestation uploads, the ci.yml timeouts — is written,
structurally tested against the parsed workflow graph, and settled only by one
real `workflow_dispatch`.

Two further statements belong here rather than in a release note, because both
are easy to mistake for oversights:

- **macOS has no publishable path in this version.** `verified` is unreachable
  by D11's own decision — the build ad-hoc signs and notarization is not
  attempted. The state vocabulary describes that accurately; it does not change
  it.
- **Six Web Customize routes** now answer a real status, but the wider question
  of which services may use soft dictionaries at all is untouched.

## 9. P1-A — visible product closure

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| A.6 | Tabular preview told the truth about neither dimension: `csv()` split on a hardcoded comma, so **every** `.tsv` reported one column with the whole header line as its name; and the renderer capped columns at 24 with a rows-only banner, so a 101-column table rendered 24 and looked complete. One `delimiterFor` now trusts the extension and sniffs the header when there is none (science writes tab-separated `.txt` constantly), and the banner names both dimensions | `54ef6e8` | `Completed` | Forcing `delimiterFor` to a comma fails the **browser** check with the defect's own signature (`expected 3 column(s), got 1`); removing the column banner fails the static gate |
| A.1 | ArtifactRef **backend contract**: `@name#v-<id>` sends that version's frozen bytes, never the live path; an unresolvable reference is reported instead of dropped; a binary artifact is named rather than pasted as U+FFFD; a same-project cross-session reference materialises **at send** (D3, decided with the owner) so an inserted-then-deleted chip leaves nothing behind. Legacy `@name` kept one release, session-scoped, and says it is unpinned | `221ff9b` | `Completed` | Reading the live path, dropping refs silently, or widening the legacy form to the project each fail. The composer chip is wired too: the `@` menu already listed artifacts from across the *project* while the resolver looked only inside the session, so it was offering files it could not deliver — it now inserts `name#version_id`, labels a file that will be copied in, and `artifact_ref_problems` renders inline. Driven in a real browser: 2 problem rows, server message shown |
| A.3 | Latest-first message paging. Two of this item's three parts were **already done** and are recorded as found, not as built: the session keyset cursor works (verified by paging 260 real sessions — 6 pages, no duplicates, none lost), and branch export already carries every branch's messages (`_export_messages` applies no branch filter and tags each row — read, not independently exercised). The real gap was message direction: a 640-message session returned messages 0–299, the *oldest* page | `45f7e85` | `Completed` | Forcing ascending order fails by message id; ignoring the cursor collapses 640 rows to 50 |
| A.5 | Notebook export split menu. `notebook/export` has always accepted `python`, `r` and `bundle`; the client hardcoded `?language=bundle`, so two working formats were unreachable — wanting the Python notebook meant downloading a zip and unpacking it, and nothing said the others existed. The default action is unchanged (one click, same file) with the other two behind a chevron | `43965fc` | `Completed` | Removing either format fails by name; reordering so the bundle is not first fails the default-action assertion. Driven in a real browser: menu toggles, both new hrefs present |
| A.7 | Retrieval-source read-only panel. The `artifact_versions.source` envelope has recorded the request URL, query and response hashes since retrieval provenance was added, and `list_versions` never selected the column — a figure built on a live API fetch was indistinguishable from one computed from nothing. Now allowlisted, bounded at 2000 chars per value, and redacted in the query string, the path and the userinfo; clipped and withheld fields are both counted rather than hidden | `3dcda11` | `Completed` | Removing the redaction, the allowlist, or the `source` column each fails — the last with "the provenance never reached the client", which is the trap my own first probe fell into |
| A.4 | Image attachment budgets: 8 images, 4 MiB each, 12 MiB total, measured after the pin markers are drawn because that is what goes on the wire. None existed — eight pins on a 3000×2200 raster sent ~10 MiB and nothing stopped eighty. Dropped figures are named to the user *and* to the model, which is told to say they were not received rather than describe them | `7c92a0c` | `Completed` | Raising any of the three limits fails its own test — **after** a rewrite: the first version of two of those tests defined their expectation in terms of the constant under test, so removing the budget broke nothing. Mutation testing found that, not review |
| A.2 | Readiness card (local-only: `ready`/`needs_key`/`needs_model`/`unsupported`, `checked_endpoint` always false), explicit `POST /model-profiles/{id}/probe`, and **two** providers made selectable — `gemini` *and* `openai_responses` were both dispatchable and both unreachable from the menu. Identity selection itself landed earlier in P0-2.10 | `8d715eb` | `Completed` | Making the card probe on render fails; dropping gemini fails by name; probing a keyless profile fails. **A live provider endpoint is never contacted by any test** — the success path of `probe` is `Implemented but unverified` |

## 10. P1-B — Agent, Skill and Compute control

| # | Item | Commit | Status | Falsification |
|---|---|---|---|---|
| B.4 | Specialist tri-state allowlist enforced. `None` inherits, `[]` denies all, a list is exactly those; a child may only narrow, and inheriting is not widening. Filtering covers the four surfaces the exit criterion names: catalogue, search, `load`/`get`, and `read`. **Corrected 2026-07-29** — this row previously read `Completed` / "Enforced on all four surfaces", and the filter was never armed: `set_allowed_skills` had one definition and six call sites, every one of them in `tests/test_resource_allowlist.py`. `_allowed_skills` stayed `None` — permit everything — for the life of every real dispatcher, so a specialist restricted to one Skill saw all 34 and could read any of them (measured). The tests passed because they armed the allowlist themselves. Now armed at `HostDispatcher.set_child_execution_policy`, the single choke point every child passes, and spec inheritance narrows instead of replacing so a nested child cannot widen. **Connector half built and armed 2026-07-30** — it had no mechanism at all: `connectors` reached `ChildExecutionPolicy` nowhere, so a specialist whose row read `connectors=['a']` still listed every enabled connector and could `mcp_tools`/`mcp_call` any of them through direct Host RPC, spawning the process (measured). The gate sits in `MCPService.connector()`, the single lookup all six MCP RPC methods share and the one the launch config is built from, so a denied connector has no command to be started with; `list()` is filtered too because the catalogue is what the model asks from. Both halves are armed on the same line of `set_child_execution_policy`. The same batch closes a hole in **both**: `_apply_parent_execution_ceiling` narrowed capabilities and permissions but left `skill_names`/`connectors` untouched, and it is the only narrowing that sees the parent *child's* spec — so a grandchild could widen what `_normalize_item` had bounded | `f7c108b` + this batch | `Completed` | Disarming the dispatcher fails 5 skills tests and 8 connector tests; the falsy collapse (`if not allowed:`) fails 1 here and 3 in the older file; letting a nested child replace its inherited list fails 1; removing the ceiling narrowing fails 3 |
| B.2 | Subtree stop and turn-boundary steering were **already implemented** and are recorded as verified, not built: `_stop_subtree` walks `descendants`, which follows `parent_child_id`, so siblings are structurally outside the walk, and `send_message` already queues with an explicit rejection. The exit criterion — cancelling one child does not affect a sibling — now has a test against the real runner | `624eaf5` + this batch | `Verified` | Replacing the walk with "stop everything" fails the sibling test; removing the descent fails the subtree test. The 409 now has a surface: `POST /frames/{id}/delegations/{child}/stop` and `.../steer` make the already-verified runner behaviour reachable, and keep three answers apart — **404** no such child in the record, **409** the record has it but nothing live can act on it (the ordinary post-restart state, where `daemon_restart` marks every pending child stopped), **200** done. `send_message`'s `{"ok": False}` became a 409 instead of riding out as success. Five properties falsified; success-path tests included, since a file that only asserts refusals passes on a handler wired to the wrong method. |
| B.3 | Skill `requirements` parsed and surfaced with `ready`/`needs_setup`/`unknown`. Five bundled Skills have declared `requirements: [gpu]` since they were written and **nothing read it** — not the loader, not the Skill object, not the catalogue — so a GPU-only Skill looked identical to one that runs anywhere and the agent found out at execution time. Readiness is local-only (`nvidia-smi` is looked for on PATH, never run), and sits beside `enabled` rather than inside it | `d905419` | `Completed` | Dropping the parse fails 1; guessing `ready` for an unknowable requirement fails 2; probing by running `nvidia-smi` fails 4 |
| B.6 | Memory budgets and context projection | `openai4s/memory_budget.py`, `server/gateway.py`, `tests/test_memory_budget.py` | `Verified` | The injection was `mems[:50]` — a count, and nothing else. Measured on 60 memories of a pasted protocol: **600,647 characters** added to every system prompt, roughly 150k tokens against a 262,144-token window. Now three budgets (50 items / 2,000 chars each / 16,000 total), and the model is told when items were withheld so it can say its remembered context is incomplete. Each of the four properties falsified individually. |
| B.1 | Follow-up FIFO while running | `server/gateway.py`, `tests/test_followup_admission.py` | `Verified` | The FIFO, the depth cap (64) and exact sibling-safe cancel already existed. Three refusals did not, or arrived wrong: a message had **no text cap** (the 128 MiB *session-archive* limit stood in for one, and an 8 MiB paste is persisted, replayed, and 8× the context window — compaction cannot rescue it because summarising means sending); a dangling model pin was refused inside the worker thread and reached HTTP as **200 + `{status: failed}`**; a full queue reached it as **500**. Now 413 / 409 / 429, all decided at admission before anything is written or queued. Model identity freezes when Send is pressed, not at dequeue. Four properties falsified individually. |
| B.5 | Owner-scoped remote compute task centre | `server/compute_tasks.py`, `storage/compute_jobs.py`, `server/gateway.py`, `server/webui/app.js`, `tests/test_compute_task_centre.py` | `Verified (no live provider)` | Read-only listing scoped by `owner_key`, plus an explicit per-task refresh. **Opening the page cannot contact a provider, structurally**: `compute_tasks` takes a `Store` and has no import of `ComputeManager` — which matters because in this system the probe *is* the harvest. `unknown` is never rendered as failure; pids, sandbox handles and cluster paths stay out of the projection. Six properties falsified. **Unverified:** the refresh path's success case — no test contacts a real BYOC host, so what a live provider returns is exercised by nothing here. |

## 11. Not started

All of `P2` (design freeze and real-platform experiments). By decision D8, P2
enters no public API, schema or definition of done in this version.

All of `P1-B` has landed: B.1 (follow-up admission), B.2 (delegation control
and the stale-record 409), B.3 (Skill `requirements` and readiness), B.5
(remote compute task centre) and B.6 (memory budgets and context projection)
are complete, and B.4 is complete on both halves. Its history is the useful
part: the Skill allowlist was recorded here as complete for several days while
nothing armed it, and the connector allowlist was recorded as *stored,
inherited and enforcing nothing* — accurately — for a day after that. Both are
now enforced at one choke point. What each of them could not verify is recorded
in §12 rather than left implied.

## 12. Externally unverifiable

See [`v03-decisions.md`](v03-decisions.md#externally-unverifiable). Nothing in
this file marks those `Completed`.


### Release-evidence gaps found after the audit

Four things this section recorded as closed were not, and are now:

* **Build receipts covered two artifact kinds.** `required_kinds` was
  `("dist", "macos")`, so the Linux tarball and the Windows zip were staged with
  nothing binding their bytes to the frozen commit — covered only by the in-run
  `incoming` digests, which attest that `attach` downloaded what it downloaded.
  The kinds are derived from the assets now, and the two build jobs write them.
* **The Python matrix compared three files.** `build_linux_bundle.sh` was the
  fourth and nothing read it.
* **A plain `--mode release` run answered the quality question with a local
  `pytest -q -x`** — no pre-commit, no mypy, no README check, no harness tier,
  no response schema or contract, no secret scan, no attestation — and then
  staged assets onto the draft. `--from-artifacts` could not bypass the quality
  receipt; this path never consulted it. It does now, and `step_build` stops
  deleting the receipt it needs: that document is an input to the run, not an
  output of it.
* **Every evidence bundle shipped without its SBOM.** `step_sbom` writes
  `sbom.cdx.json`; the collector asked for `sbom.spdx.json`, a name nothing here
  has ever produced, and the `if path.is_file()` filter dropped it silently. The
  step reported success on every release.

Still open and recorded rather than claimed: `platform_checks` receipt rows are
always `[]`. The platform-checks job really does run the sandbox smoke at the
frozen SHA and really does gate `build` through `needs`, but it is a sibling of
`quality` in the graph, so the job that writes the receipt cannot see its
result. Closing it needs an artifact hop between the two jobs, which is a
workflow change no working copy can verify.

## 13. Post-v0.3 audit remediation

A two-round multi-agent audit of this repository produced 41 candidate defects;
adversarial verification confirmed 28 and refuted 13 (8 on mechanism, 5 on
consequence). Every fix below was reproduced before being made and falsified
after, one property at a time.

| Severity | Defect | Commit |
| --- | --- | --- |
| High | provenance side table keyed on `id()` — a freed object's lineage inherited by an unrelated one, on the first allocation | `5c28437` |
| High | every saved specialist failed to delegate: SQLite's `int` met a strict `bool` check | `c8ce530` |
| High | `web_fetch` redirects escaped the SSRF/egress guard on the stdlib path | `c8ce530` |
| High | all nine `openai4s share` subcommands 404'd on an unversioned API root | `c8ce530` |
| High | an oversized `error` dropped the response frame and hung the kernel | `c6fb624` |
| High | `prov_record` published any absolute host path as a session artifact | `dce5ff4` |
| High | an enforced sandbox exposed the daemon access token and the macOS keychain | `dce5ff4` |
| Medium | the biosecurity trajectory screener never ran on the Web daemon | `b8dcad4` |
| Medium | the R variable inspector reported every variable as `symbol` | `8239f1f` |
| Medium | R read captured output whole before capping; reported a false `0` peak RSS; the loader escape was UNSAFE in Python and SAFE in R | `eb5fc53` |
| Medium | the managed-endpoint readiness probe was an unguarded SSRF oracle | `52e2833` |
| Medium | eight `@` references could add 1.6 MB to one prompt | `a13acce` |
| Medium | opening a project showed a blank new session instead of its sessions | `2165438` |
| Medium | `attachment_problems` was emitted to a client that never listened | `adfa082` |
| Medium | a fan-out to a specialist dropped the specialist's prompt | `37d9293` |
| Medium | a delegated child compacted against the daemon default, not its own model | `f2652bd` |
| Medium | a remote job in a cell that wrote nothing became the next cell's provenance | `669c1e0` |
| Medium | R cells emitted no `stdout_chunk`, so live output was dead for the R half | `2c3fae3` |
| Medium | `stop_kernel` queued its lifecycle ticket behind the executions it was cancelling | `7322140` |
| Medium | `generation_confidence` and `provenance` were written by a migration and read by nothing | `PENDING` |
| Low | `Tool.dangerous` was declared on ten tools and read by no gate, audit or prompt | `PENDING` |
| Low | `host.app_render` grew without bound (100 MB measured); a released idle session kept its history | `PENDING` |
| Low | model-profile `readiness` and the probe route had no UI call site | `PENDING` |

### Found by the pre-merge aggregate review (2026-07-29)

Eighty-seven commits were each reviewed and tested individually and CI was
green; six lenses then read the range as **one change**, and every finding was
handed to an independent adversarial verifier. 21 raised, 15 survived
refutation, 3 distinct blockers — each found by two lenses independently. None
was a test failure: all eight gates were green throughout, because these are
unreachable-code and no-recovery-path defects.

| Severity | Defect | Note |
| --- | --- | --- |
| Blocker | Deleting a model profile permanently bricked every session pinned to it: 409 "choose one to continue" with nothing able to choose | delete now releases the bindings; `POST /frames/{id}/model-binding` answers the other trigger; the client can act on the code |
| Blocker | The auth gate was on by default and nothing that hands a human a URL was updated — `GET /` and `/static/app.js` both 401, so the SPA could not load to offer a way in | `openai4s url` carries the token; a browser navigation gets an actionable HTML page |
| Blocker | `set_allowed_skills` had one definition and six call sites, all in one test file — a restricted specialist saw all 34 Skills and could read any | armed at `set_child_execution_policy`; spec inheritance narrows so a nested child cannot widen |
| Blocker | The connector allowlist had no mechanism: a specialist limited to one connector listed, discovered and called every enabled one over direct Host RPC, spawning the process | `ChildExecutionPolicy.connector_names` + `MCPService.set_allowed_connectors`, enforced in the shared `connector()` lookup and armed on the same line as the Skill half |
| Medium | The model pin was write-only: a session pinned to A after B was activated dispatched to B's endpoint, model and credential | `_llm_cfg` honours the pin, falling back on anything unresolvable |
| Medium | Latest-first paging had no client: a 640-message session opened on messages 0–299 | four call sites through one helper that also restores reading order |
| Medium | An 8 MB frame backstop counted bytes against caps counted in characters — CJK output within every documented limit lost stderr, the exception text, `error_lineno`, `guards` and `usage` | derived from `MAX_OUTPUT`; the first constant (6) was itself wrong and a test caught it |
| Medium | `web_download` was the only `writes_files` tool with no `secret_path_key`, so it could overwrite a `.env` that `write_file` refuses | declared; asserted over every file-writing tool |

### Still open from the audit

**Re-checked at `408098f`: none of the four is still open.** This section said
"Four", and before that it said "Nothing" while four rows sat above it reading
`PENDING`. Both were wrong at the time; the first is wrong now, which is why it
is being corrected rather than left as a conservative overstatement — a defect
list that names fixed things is as unreadable as one that omits broken ones.

| Severity | Defect | Status at `408098f` |
| --- | --- | --- |
| Medium | `generation_confidence` and `provenance` were written by a migration and read by nothing | closed — read by `app.js` (the version pane gates on `=== "verified"`) and by `openai4s/benchmark/steps.py` |
| Low | `Tool.dangerous` was declared on ten tools and read by no gate, audit or prompt | closed — `host_dispatch.py` carries it into the audit record, and `app.js` renders the high-risk permission badge and shortens the grant to once-only |
| Low | `host.app_render` grew without bound (100 MB measured); a released idle session kept its history | closed — `MAX_APP_TILE_CHARS` refuses an oversized payload and `MAX_APP_TILES` evicts oldest-first, reporting `dropped` so a cell cannot mistake `tiles()` for the full history |
| Low | model-profile `readiness` and the probe route had no UI call site | closed — Customize → Models renders the readiness card and the explicit probe; the protocol menu carries all five protocols including Gemini, verified in a real browser at this SHA |

### Deliberately not fixed

bubblewrap keeps the host PID namespace so `Kernel.interrupt()` can target
`Popen.pid` exactly. The credential-bearing half is closed —
`/proc/<daemon>/environ` is masked after the `--proc` mount — and the rest is
recorded in [`v03-decisions.md`](v03-decisions.md) rather than attempted blind
on a platform this is not developed on.

## 14. This batch (2026-07-29)

Two findings landed, both on complete production call chains, both previously
recorded above as `Completed`. Full detail is in the commit message; what belongs
here is the status vocabulary applied honestly.

| Plan item | What changed | Status | What is missing |
|---|---|---|---|
| P0-0 items 1-8 | Canonical gate manifest shared by producer and consumer with exact-match verification; `freeze` job peels the tag once and every job checks out that output; `--source-sha` checked against the checkout; per-artifact build receipts binding bytes to the frozen commit with builder OS/arch/interpreter (originally only `dist` and `macos` — the Linux tarball and the Windows zip were staged with no such document, and the required kinds are now derived from the assets present); evidence bundle promoted to a mandatory step before `checksums` and `upload`, carrying the receipts, artifact digests, sandbox posture and builder, re-verified with the product's own `evidence.verify_package`; out-of-band stage attestation so `finalize` no longer trusts the draft's own `SHA256SUMS`; notarization read from `xcrun stapler validate` and required for a public release; `timeout-minutes` on all ten ci.yml jobs; browser and Python-matrix gates bound to the release SHA by check-run attestation with run ids recorded; platform sandbox checks executed at the frozen SHA | `implemented_unverified` | One real `workflow_dispatch`. The script-level logic is falsified offline (`tests/test_release_gate_manifest.py`, 42 cases including retag-between-jobs, missing/duplicate/unknown/substituted gate, seal failure, Developer-ID-without-notary, and asset+manifest replaced together); the YAML is asserted against the parsed workflow graph, not executed |
| P0-2 items 9-11 | Agent SQL moved to a separate `mode=ro` connection under a real SQLite authorizer; artifact family reachable only through session-scoped `my_*` views; foreign and absent artifact refusals made indistinguishable; `view_image` scope-checked; `input_version_ids` validated before any copy or row | `closed` | Nothing for these three. `tests/test_artifact_scope_closure.py` (29 cases) covers direct SQL, CTE, five quotings, bound-parameter and `pragma_*` bypasses, the catalog by rule, and both refusal-indistinguishability directions |
| Plan section 14 | The 56-item matrix became [`plan-crosswalk.json`](plan-crosswalk.json) | `closed` | Nothing. `tests/test_plan_crosswalk.py` enforces 56 unique keys each appearing once, eight per source report, `closed` naming an existing test file, and `implemented_unverified` naming its missing run |

### The offline suite is not green at HEAD, and was not before this batch

`uv run pytest` fails one test at `120af6a`, the commit this batch started from:

    tests/test_r_kernel.py::test_interrupt_returns_interrupted_result_and_keeps_worker

It passes in isolation and in any small combination, and fails in a full run —
`RuntimeError: kernel worker exited unexpectedly`, the `fake_rscript` stand-in
dying, at around thread #1045. Measured both ways in matched, isolated
environments (Python 3.12.13, `--extra science`, each tree importing its own
source): `120af6a` fails it in 817 s, this batch's tree fails the same single
test in 800 s. **This batch introduces no new failures**, and the failure is not
one it caused.

Recording it here because §13 above states "all eight gates were green
throughout", and that is not true of this gate. Two things made it easy to miss,
and both are worth naming:

- Every early full-suite invocation in this work was written as
  `uv run pytest -q 2>&1 | tail -6`. In a pipeline the shell reports the *last*
  command's status, so the exit code observed was `tail`'s — always 0. A red
  suite and a green one produced the same signal.
- The failure needs the whole suite. Any per-module or per-directory run passes,
  which is the same trap `tests/README.md` already documents for global `Popen`
  patches.

Not fixed here: it is a cross-test resource or patch interaction in the R kernel
harness, unrelated to identity, scope or release evidence, and diagnosing it
means bisecting ~7000 tests. It belongs in its own change.

### Deliberately not claimed

P0-2 item 12 (materialisation/upload atomic boundaries), item 13 (immutable model
profile revision), items 14-15 (compute owner scope and result harvest), all of
P0-3 (items 19-22), all of P0-4 (items 16-18) and all of P1-A/P1-B (items 23-31)
were audited on their production call chains and are recorded `open` in the
crosswalk with the located defect. They are not started in this batch. Marking
them anything else is the error this section exists to stop repeating.

## 15. P0-2 items 12-15 (2026-07-30)

| Plan item | What changed | Status | What is missing |
|---|---|---|---|
| 12 — atomic boundaries | Materialisation refuses a same-name live file instead of `unlink()`ing it silently, verifies the source snapshot through the shared held-FD checksum/size/single-link reader, writes private durable copies, and rolls back symmetrically. The live file is a real **copy**: it was hardlinked to the source session's immutable snapshot, so one ordinary write through the borrowing session's working file rewrote another project member's frozen bytes and left that version's checksum describing bytes that no longer existed (measured, not reasoned). Upload resolves scope before touching disk; its immutable pending snapshot remains pinned through promotion and commit, and the recovery journal binds both live and final-snapshot inodes | `closed` | Nothing for item 12. `tests/test_materialisation_atomicity.py` covers private snapshot/live inodes, same-length source tamper, refusal-before-mutation, symmetric rollback, staging debris, and "a rejected upload writes nothing to disk at all"; `tests/test_upload_scope_resolution.py` covers pending/final name swaps and crash recovery |
| 13 — profile identity | `_pinned_llm_config` no longer prefers the request's bare `model` (the browser sent it on every message, producing a config in no profile), no longer returns `None` on a revoked key or any exception (which silently ran the turn on the globally active profile), `models_payload` is keyed on `profile_id` rather than deduped on bare model names, `PUT /models/default` activates a profile, `app.js` stopped sending `model` per turn, and delete is a tombstone | `closed` | Nothing. Sub-defect (5) closed separately below |
| 14 — owner-scoped idempotency | `by_idempotency_key` takes an owner and scopes like `live(scoped=True)`; the UNIQUE index is `COALESCE(owner_key,'')` because SQLite treats NULLs as distinct and NULL is exactly the CLI rows; migration 11 builds the new index before dropping the old one | `closed` | Nothing. Verified on a real pre-existing v10 database, not only on a fresh one |
| 15 — harvest capture | `compute_result` declares `writes_files`, which is the only thing the Web wrapper gates on; `refresh_compute_task` brackets the harvest with snapshot/capture | `closed` | Nothing. A live BYOC provider is still never contacted by any test — what a real provider returns is exercised by nothing here |

### A prior decision reversed, deliberately

§13 above records a Blocker fix: "Deleting a model profile permanently bricked
every session pinned to it ... delete now releases the bindings". Item 13 requires
the opposite mechanism — a tombstone that preserves the revision history — because
releasing the pin destroys the audit answer to "what configuration did this session
run under", and makes the next send re-pin somewhere else silently, which is what
D2 exists to prevent.

The anti-brick requirement is **not** dropped. It is now met by the route that was
added alongside that fix: `409 model_revision_unavailable` followed by
`POST /frames/{id}/model-binding`. The test that guarded the brick asserts the
whole path — refused, then explicitly rebindable, then sendable — rather than being
deleted. `activate` also refuses a tombstone, so a deleted profile cannot be made
live again with an empty credential.

If auto-release is preferred after all, it is a one-line revert in
`ModelProfileService.delete`; the trade is stated here so it is a decision rather
than a regression.

### Item 13 sub-defect (5): the queued follow-up

`submit_message` did freeze the model identity at send -- its comment says so, and
it was true. What it froze onto was the **frame**, and the frame's pin is mutable
*by design*: `POST /frames/{id}/model-binding` rewrites it, because that route is
the documented answer to a dangling pin. So the sequence a user can actually
produce was:

1. a follow-up is accepted under profile P; the client is told 202;
2. the user rebinds the session to Q, which is a supported action;
3. the item reaches the head of the FIFO, `run_message` calls
   `bind_model_revision` again, reads the frame, and dispatches to **Q**.

Nothing told the client the work ran on something other than what it was accepted
under -- the failure mode D2 exists to prevent, arriving through the one path that
is allowed to change a pin.

Closed by freezing the pair onto the ticket rather than only the frame:
`MessageJob` carries `model_profile_id`/`model_profile_revision`, `submit_message`
sets them from a named `freeze_model_binding()` seam, the worker thread passes them
into `run_message(frozen_binding=...)`, and `_pinned_llm_config` reads the frozen
pair **before** the frame. A direct (unqueued) turn still binds from the frame,
which is the freshest answer there is for it.

A queued item whose frozen profile was deleted or whose key was revoked in the
meantime raises the same `409 model_revision_unavailable` where the job's error
surfaces, rather than silently running elsewhere: it cannot run, and saying so is
the only honest outcome once 202 has been returned.

Falsified: making `_pinned_llm_config` ignore the frozen pair fails
`test_a_rebind_while_an_item_is_queued_does_not_move_that_item` by dispatching to
Q's model. Three cases covered -- the ticket records its binding, a mid-queue
rebind does not move the item, and a dead frozen profile fails visibly.

## 16. Browser evidence (2026-07-31)

Run against a real daemon at `60298b719cb87f3610fdd667bb8a6ce06a039542`, on an
isolated `OPENAI4S_DATA_DIR`, with the auth gate on (the daemon answered `401` to
an unauthenticated `GET /`, and the harness logged in through the `?token=`
bootstrap):

| Check | Engine | Result |
|---|---|---|
| `tests/browser_smoke.mjs` (full workbench walk) | chromium | passed |
| `tests/browser_matrix.mjs` | chromium | 9/9 |
| `tests/browser_matrix.mjs` | firefox | 9/9 |
| `tests/browser_matrix.mjs` | webkit | 9/9 |
| `tests/browser_p1_controls.mjs` (P1-A/P1-B controls) | chromium | passed |

The P1 control file was added after an audit found that **none** of the three
existing browser files mentioned any of `before_seq`, `newest_first`, chip,
profile, attachment, delegation, steer or memory — the whole P1-A/P1-B control
group rested on a single manual walkthrough taken 43 commits before the audit,
which is not evidence of the code as it stands now. It covers those eight and
only those eight; the remaining crosswalk rows whose `browser_evidence` is empty
are still empty, deliberately, rather than pointed at a file that does not drive
them.

Playwright 1.54.1; chromium 139.0.7258.5. The matrix covers app-shell boot,
session create, WebSocket connect/receive, artifact projection, consent
serialise-and-reconcile, consent rollback on a failed write, cancel, the recovery
projection, and **no uncaught page errors** — the last is what makes this
meaningful for nine commits' worth of `app.js` change, because a JavaScript
exception on load would surface there rather than as a silently dead control.

Two entries in the daemon log, neither a defect: `LLMError: no API key configured
for provider 'ark'` (correct — the isolated data dir has no credential, and a turn
that cannot dispatch says so) and a `ConnectionResetError` from a browser closing
its connection.

### What this does NOT establish

The smoke and the matrix exercise the shell and the transport. They do not click
through the specific controls this batch added, and no test here does:

- the load-more control firing with more than 100 sessions, and the scroll anchor
  holding when an older page is inserted above (item 25);
- the `@` menu offering two artifacts that share a filename (item 23);
- the `attachment_problems` card rendering its four reasons (item 24);
- the composer staying usable mid-run, and a queued item showing its execution id,
  preview, position and frozen branch/profile (item 26);
- the protocol dropdown offering `gemini` and `openai_responses` (item 27);
- the Skills list rendering `needs_setup`/`unknown` (item 28).

Each is named in its commit. "No uncaught page errors across three engines" is a
real and previously missing floor -- it rules out the whole class of failure where
a change to `app.js` breaks the page on load -- but it is a floor, not a
demonstration that these controls do what they say.

## 17. This batch (2026-08-01) — merge with main, then the Plan's remaining gaps

Ordered as it happened, because the merge is what made the rest possible.

**`origin/main` merged into `next`.** `next` had absorbed the v0.3 squash (#52)
as a plain commit rather than a merge, so git's merge base sat before both and
every file the squash touched came back as an add/add conflict against work that
already supersedes it. Fifty-two conflicts, thirty-six of which had no main-side
change at all after `f2d8adb` — verified mechanically, not by eye. Main's real
work is preserved: the BYOC confinement probe failing closed, the `$HOME`
read-class denial and its xattr channel, Linux and Windows packaging with the
bundle contract, the `wsl.exe` launcher exit code, and the example-session
contract widening. Two of main's decisions are accepted as deletions: the
gitleaks history scan and its allowlist are gone, and the working-tree source
secret scan carries the load.

The merge also recovered `120af6a`'s rendezvous deadline, which the sync commit
after it had reverted, and re-introduced one block it should not have — a v0.3-era
`release_model_binding` in `model_profiles.py` that the P0-2 immutable-revision
work had removed. Five tests were red on it. A three-way merge against a
superseded side can restore deleted code silently; the check that catches it is
"the merge result must equal ours wherever main contributed nothing".

| Plan clause | What was actually missing | Status |
| --- | --- | --- |
| P0-3 local job | No deadline at all (`MAX_ACTIVE_JOBS` said so in its own comment), the output cap applied *after* an unbounded `readline()`, no `close()`, nothing wired into `server_close`, no receipt across a restart, and `str(e)` on two public surfaces | `closed` — `tests/test_local_job_lifecycle.py` |
| P0-3 MCP budgets | `_MAX_FRAME_BYTES` counted characters off a `text=True` pipe, one `read(1)` per character: a frame at the limit was a four-million-element list of one-character strings | `closed` — `tests/test_mcp_lifecycle.py` |
| P0-4 plan state | `approve` had the read-then-write race `resume` was fixed for and none of the fix; two POSTs both got 202 and both turns ran the same steps | `closed` — `tests/test_plan_resume_claim.py` |
| P0-4 error truth | Three public bodies answered with the OS's words: `restore failed: {error}`, `write failed: {error}`, and the attachment problem card | `closed` — `tests/test_public_exception_projector.py` |
| P1-B memory | Expiry withheld a memory and kept its quota slot; and a memory could be written and deleted, never corrected | `closed` — `tests/test_memory_edit_and_expiry_quota.py` |
| P1-A attachments | The annotation version binding did not survive a checkpoint restore — captured by `SELECT *`, restored by an explicit column list without it | `closed` — `tests/test_checkpoint_binding_survival.py` |
| P1-A ArtifactRef | The chip existed on the *sent* message only; before the send a pinned reference was prose in a textarea | `closed` — verified by clicking against a real daemon |
| P1-A export | Three export forms, all for re-running; none for reading | `closed` — `tests/test_notebook_export.py` |
| P0-0 release | `linux-app` and `windows-package` arrived from a branch with no `freeze` job and checked out a moving ref, while P0-0's whole claim is one immutable SHA | `closed` — `tests/test_release_gate_manifest.py` |

**One defect this batch found only by driving the product.** `POST /uploads`
answered `500 internal_error` for every session outside the `default` project:
`upload` read `payload.get("project_id") or "default"`, so a request that named
a frame and no project asserted `"default"` on the caller's behalf, and the
scope resolver — correctly — refuses a stated project that disagrees with the
producer frame. The refusal then escaped as a bare `ValueError` and became a
500. Neither half is visible in a test that passes `project_id` explicitly,
which every existing upload test did; it surfaced on the first real upload
during browser acceptance. Closed by `tests/test_upload_scope_resolution.py`.

**The release gate itself stays `implemented_unverified`.** Nothing in this batch
changes that: the YAML is asserted against the parsed workflow graph, and one
real `workflow_dispatch` is still the missing run.

## 18. Browser evidence (2026-08-01)

Run against a real daemon at `2947bec9786633359e0a693ba9d6f5e637ecfdeb`, with
the auth gate on — the daemon answered `401` to an unauthenticated `GET /`, and
both harnesses logged in through the `?token=` bootstrap, so the 303 and the
cookie hand-off are on the path too.

| Check | Engine | Result |
|---|---|---|
| `tests/browser_smoke.mjs` (full workbench walk) | chromium | passed |
| `tests/browser_matrix.mjs` | chromium | 9/9 |
| `tests/browser_matrix.mjs` | firefox | 9/9 |
| `tests/browser_matrix.mjs` | webkit | 9/9 |

`browser_smoke.mjs` needs `OPENAI4S_NOTEBOOK_REPL=1`, the same variable CI sets.
A daemon started without it fails the run on `kernel/interrupt` with `403
notebook REPL is disabled` — an environment precondition, not a product defect,
and worth writing down because the failure reads like one.

### Driven by hand, in the browser, at the same SHA

Each of these is a click or a real request through the running product, not a
unit test standing in for one.

| Path | What was observed |
|---|---|
| ArtifactRef composer chip | Typing a partial `@figure_cell3` drew the chip in its unresolved state; accepting the autocomplete flipped it to resolved with `v-f85486107c9c · sha256:15e83e6a4e08`. Both halves matter — the unresolved state is the whole reason to draw it before the send. |
| Same-name Artifact selection | The `@` menu lists project artifacts keyed by `artifact_id`, each row carrying its short version id, so two files sharing a name are separately pickable. |
| Notebook language selector | All four export forms are real hrefs; `?language=markdown` came back `200 text/markdown; charset=utf-8`, 12,519 bytes, with the structured per-cell headings. |
| Models protocol menu | Five protocols including Gemini; the session model list is provider-qualified, so same-named models do not collapse. |
| Skills readiness | `alphafold2` renders `本机缺少: gpu` — a readiness verdict computed from declared requirements, separate from the enable toggle. |
| Memory scope and edit | Scope selector plus the injection counts (injected / omitted / inherited / overridden); the new pencil edited a row in place and it came back carrying the `edited` pill, without losing its position. |
| Table truncation banner | A 5001×101 TSV renders `共 5,001 行 × 101 列 … 1 行、1 列未显示` and reports the true shape rather than the displayed one. |
| Retrieval-source panel | A version created through a REPL cell calling `host.save_artifact(source=…)` renders all eight allowlisted fields, with `apiKey=` redacted to `<redacted:7314196d6cc3>` and the non-allowlisted field withheld and counted as `另有 1 个字段未展示`. |
| Dual-tab repaint | A rename issued in one tab repainted a second tab over the WebSocket with no reload. |
| Three-item queue, cancel the middle | Three `user_repl` tickets at positions 0/1/2; cancelling the middle over the real WebSocket returned `ok: true, scope: "queued"`, the running ticket kept `cancel_requested: false`, and the third survived and moved up — siblings untouched, FIFO preserved. |

### What this run does not establish

Two of the paths on the acceptance list could not be driven here, and neither
is recorded as verified.

**The composer follow-up queue strip** renders queued *agent messages*. Queuing
one requires a turn to be running, which requires a provider credential this
working copy does not hold and which is not ours to supply. The queue machinery
underneath it is exercised: `browser_smoke.mjs` asserts the Agent queuing behind
a REPL cell, its automatic admission and its terminal state, in Chromium against
this daemon, and `tests/test_queued_followups.py` covers the per-item cancel and
the FIFO order the strip displays.

**The remote compute task centre populated with a real record** requires a BYOC
submission. The route's central promise is verified live — opening it answers
`polled: false`, because the probe is the harvest and a page that refreshed
itself would bill a provider on a schedule nobody chose — but the list is empty
in this data directory. Seeding a row by writing to the store directly would be
evidence about a fixture, not about the product, so it was not done;
`tests/test_compute_task_centre.py` carries the owner isolation, the restart
survival and the `unknown`-is-not-failure rendering.

One defect was found by this run rather than by any test: `POST /uploads`
answered `500` for every session outside the `default` project. It is fixed and
recorded in §17.

## 19. The audit backlog, T0–T5 (2026-08-03)

Recorded here because §18 above was the last entry and it stops at
`2947bec9786633359e0a693ba9d6f5e637ecfdeb`. **Sixty-seven non-merge commits
landed after it and none of them appeared in this file** -- the admission
ledger, the diagnostics-bundle redaction, R-cell truncation, the eight-refusal
permission fix, and everything in T1–T5 below. A progress record whose last
section is two months of work behind does not read as incomplete; it reads as
"nothing has happened since", which is a statement rather than a gap.

Audited at `39bc788aa01d`, the same commit `plan-crosswalk.json` now declares.
This is a *grouped* record, not one row per commit: the per-item detail lives in
the commit messages, and restating sixty-seven of them here would be a second
copy that drifts from the first.

### T0 — clearing the floor

Uncommitted work landed; the Linux-sandbox release gate had no passing hosted
evidence, so requiring it blocked every release rather than a bad one. It was
declared unproven in `release_gates.PLATFORM_CHECKS_UNAVAILABLE` and carried in
the evidence bundle instead of dropped. The later restricted bwrap profile may
change the old runner result, but the raw-network interrupt job does not answer
the full-boundary question.

### T1 — the security boundary an agent can actually reach

Five closures, each on a call chain reachable from a cell: an agent could name
any SSH destination and the daemon dialled it (fixed in three places, not one);
`prov_resolve_path` answered existence questions across every project;
cross-session artifact reads had no capability gate and the inline cap sliced a
buffer that had already been read whole; the Specialist allowlist missed the
prompt surface its own exit criteria named; and two public surfaces still
printed exception text.

### T2 — lifecycle and resource correctness

`host.bash`'s deadline bound the shell and not the process group it left behind;
the kernel manager's stderr was an unbounded `readline`; `cancel` could overwrite
a published `timeout`; materialisation and capture double-registered, putting the
lineage edge on a superseded version; plan resume re-ran every step the agent had
deliberately skipped; and the decision route was a second, unstructured failure
shape.

### T3 — contract completion

A legacy session that matched no profile was silently pinned to whatever was
active; an endpoint's credentials were stored, published by `GET /model-profiles`
and sealed into an immutable revision; a `docs/v03-decisions.md` env override
nothing read; and the two channels that bounded their input correctly and then
described the result wrongly -- an artifact reference cut to fit that reached the
model reading as the whole file, and an MCP connector that answered promptly and
too largely being reported, and evicted, as one that had timed out.

### T4 — evidence and coverage

The implementations were right; the proofs were not. P0-1's exit matrix had no
unauthenticated WebSocket upgrade, a one-route REST "matrix", and a
cookie-across-restart case that asserted token-file stability. The MCP wrong-ID
flood budget had zero call sites outside production. All eight P1-A/P1-B controls
had zero browser coverage. Four of the nine CI gates were steps inside another
job and so could not fail on their own. And the release evidence bundle was
missing build receipts for two of four artifact kinds, compared three of four
interpreters, let a plain `--mode release` stage assets on a local `pytest`
alone, and shipped without the SBOM it built every time.

### T5 — documentation honesty

`plan-crosswalk.json` re-audited: one paragraph had been pasted onto four
`closed` rows, three of which it did not describe; `browser_evidence` was empty
on all 56 rows; and 25 of the 48 closed rows rested on a test file modified after
the audit the document declared. Each closed row now carries a digest of its
evidence and `scripts/reaudit_crosswalk.py` is the only thing that moves it. This
file's own duplicate `## 15` -- with `## 16` sitting between the two, and a `§15`
cross-reference that could mean either -- is fixed, and
`tests/test_progress_document.py` keeps both properties.

### What this section does not claim

It does not upgrade any status above. The five `implemented_unverified` rows
still wait on one real `workflow_dispatch`; the macOS notarization still waits on
a certificate; `platform_checks` receipt rows are still `[]`; and BYOC state is
still absent from the release evidence. Those are named in §12 and remain there.
