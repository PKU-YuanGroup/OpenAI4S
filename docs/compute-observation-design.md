# Compute job observation — read-only contract and acceptance preparation

[中文](compute-observation-design_zh.md)

Status: **D2 preparation only — not implemented in this version**. This document
separates, as a future contract, a single-task read-only observation of a
`host.compute` job from the explicit result retrieval that exists today. It adds
no route, response field, poller, persisted column, provider operation or
production code, and it changes neither `ComputeManager.result()` nor any
default switch. **This version does not implement any part of it.** The cases
below are requirements for a later implementation, not test results; today's
listing, refresh, cancel and result behavior keeps its meaning.

## Current boundary

**The probe is the harvest.**
[`ComputeManager.result()`](../openai4s/compute/manager.py) is the only path that
asks a provider how a `host.compute` job is doing. It probes the remote and, once
the work is terminal, pulls the outputs into `<workspace>/hpc/<job_id>/`,
commits the terminal state, exit code, manifest and digest, and closes the job.
`POST /api/v1/frames/{fid}/compute/tasks/{job_id}/refresh` is exactly that call
wrapped in Artifact capture, which is why it is an explicit POST rather than a
page refresh. There is no read-only provider probe behind it.

**The listing cannot reach a provider.** `GET /api/v1/frames/{fid}/compute/tasks`
calls [`compute_tasks.owner_tasks`](../openai4s/server/compute_tasks.py), which is
handed a `Store` and has no import of `ComputeManager`; it reports
`polled: false` and cannot advance a row however it is called. `public_task`
exposes only a bounded projection: an allowlist of stored fields (`job_id`,
`provider`, `status`, `reason`, `termination_reason`, `exit_code`, `created_at`,
`updated_at`, `submitted_at`, `terminal_at`, `integrity_sha256`) plus computed
`live`, `terminal` and an `outputs` manifest summary; its `updated_at` is when
the state was last *established*, and the module deliberately omits a read time
so a stale row cannot look freshly confirmed.

**Every transport couples state and bytes.** The SSH path runs one bounded probe
script (`kill -0 <pid>` → `RUNNING`; else `.rc` → `RC <code> [TIMEOUT]`; else
`NORC`) and then harvests over SCP/tar inside the same `result()` call; an
unreachable host returns `status: unknown` with `error_kind: unknown_state` and
is never cached. The BYOC path dispatches the resident `wait` op (bounded
`.phase` poll, then streams `out.tar.gz` out of the sandbox) and publishes the
harvest in the same call; missing terminal evidence is also `unknown`, never
`failed` and never cached. The provider contract itself
([`ByocProvider`](../openai4s_compute_provider/_protocol.py)) offers
`create_sandbox`, `exec`, `list_owned`, `read_owner`, `terminate` and optional
`list_dir`/`list_volumes`/`read_file` — no "get job state" operation. The
resident has a probe-only helper path (`_op_wait` with `probe_only`,
`_op_probe_many`) intended for a host status poller, but no host caller exists
today: it is helper plumbing, not a public observation capability, and even it
executes a remote command.

**A separate control plane exists and is deliberately outside this design.**
[`openai4s/orchestration/`](../openai4s/orchestration/) owns Team Server batch
workloads: `SlurmBackend.observe(allocation)` returns a normalized `Observation`
(`phase`, `reason`, `handle`, `diagnostics`), the reconciler turns it into durable
facts, and [`orchestration_routes.py`](../openai4s/server/orchestration_routes.py)
exposes those workloads. That observation is a scheduler read internal to the
allocation lifecycle. It is not a `host.compute` job status, it never appears in
`GET …/compute/tasks`, and its `Phase` vocabulary is not `compute.states`. This
document neither extends it to `ComputeManager` jobs nor treats it as the
compute-task observation contract.

**Identity is owner-scoped and partly remote.** A job row carries `job_id`,
`provider` (`ssh:<alias>` or `byoc:<id>`), the submitting session's `owner_key`
(its workspace path), and the remote handle `receipt`/`alias`/`workdir`/`pid`/
`sandbox_id`. `_rehydrate` loads only this owner's live rows, and `_jobs` is
owner-scoped, so another session's job id resolves to `not_found` — deliberately
indistinguishable from a job that never existed. `host.compute.status()` answers
`{live, limit, daemon_live, provider_caps}` from local counters, not remote
truth; `reconcile()` lists jobs that were live when the daemon stopped and
resubmits nothing; `job_history()` reads the append-only event stream. The native
`compute_result` tool is `read_only = False` and `writes_files = True`; cancel and
submit are writes as well.

| Surface | Current behavior to preserve or distinguish |
| --- | --- |
| `ComputeManager.result()` | Probe plus harvest plus terminal commit in one call. Returns recorded evidence with `cached: True` for an already-terminal job without contacting the provider. |
| `POST …/compute/tasks/{job_id}/refresh` | The explicit write path: `result()` plus `ArtifactManager.capture`, Stage 11 receipt binding where that stage is enabled, `polled: true` in the response. |
| `GET …/compute/tasks` | Durable-record read only; owner-scoped; `polled: false`; zero remote action. |
| SSH transport | Bounded probe then harvest, same call; unreachable host or missing exit code is `unknown`, not `failed`. |
| BYOC transport | Resident `wait` op then in-sandbox archive harvest, same call; missing terminal evidence is `unknown`. |
| Provider contract | No status operation; state lives in the host's wrapper files (`.phase`, `.rc`, `.timeout`) and needs a remote `exec` to read. |
| Owner scope | `owner_key` is the session workspace; another session's id is `not_found` with no existence confirmation. |
| SDK `status()` / `reconcile()` / `job_history()` | Local daemon counters; last-stop live rows with no resubmission; append-only event read. |
| `compute_result` tool and SDK `.result()` | Write-capable: harvests files, may commit terminal state; not an observation call. |

## Observable and unsupported surfaces (future)

A future observation must be its own read-only capability with its own contract,
not a wrapper around `result()` and not the stored row re-labelled as fresh. The
provider boundary decides whether an observation is possible at all; when it is
not, the answer is "unsupported", never a guess.

| Proposed surface | Proposed observation contract |
| --- | --- |
| Durable record (`status`, timestamps) | Observable locally with no remote call. It is recorded evidence, reported as such, and never presented as current remote truth. |
| SSH process liveness (`pid`, `.rc`, `.timeout`) | Observable in principle with a probe that returns no bytes and writes nothing. Today the probe exists only inside `result()`, coupled to the harvest. |
| BYOC sandbox job state | Depends on the provider and the resident helper. The probe-only path exists but is unwired; every probe is still a remote `exec`. A provider that cannot answer yields `unsupported`. |
| Scheduler or queue position for a `host.compute` job | Not modelled: the SSH transport runs its wrapper under `nohup`, and no provider contract exposes a queue. The separate `openai4s/orchestration/` control plane observes its own scheduler allocations — a different subsystem, not a compute-job observation. |
| Remote file manifest or digest | Not observable without transfer. An observation must not imply that file contents were seen; only explicit retrieval harvests and hashes. |
| Terminal outcome | Established only by a probe that can read the wrapper's evidence. A stored terminal result is the recorded outcome and may be replayed with a `cached` marker, but a replay is not a new observation. |
| Unreachable remote | Observable only as "could not observe": `unknown`, with a bounded reason. The job may still be running and billing. |
| Another session's job | Never observable. The owner-scoped not-found answer is kept, and the observation must not confirm existence. |

## Identity binding (future)

An observation result must name the exact identity it was made for, and a
response must never be attributed to a job whose identity does not match. This
matters more than it looks on BYOC: a sandbox is warm and reused, and submitting
job B wipes `/work`, so a sandbox id alone is not a job identity.

| Identity fact | Proposed rule |
| --- | --- |
| Owner (`owner_key`, root frame) | Required on every observation; it scopes visibility, the durable row and any remote call. A mismatch is a refusal that does not reveal whether the job exists. |
| `job_id` | Must equal the durable row and the provider response. A response naming a different id is discarded, not recorded. |
| Provider target (`ssh:<alias>` / `byoc:<id>`) | Must match the row; the same job id under another provider is a different job. |
| Remote handle (`receipt`, `sandbox_id`, `alias`/`workdir`/`pid`) | Must be the handle stored for that attempt. A handle reused by later work is refused rather than mapped onto the old job. |
| Observation time | `observed_at` belongs to this observation only; it never rewrites `updated_at`, `terminal_at` or a stored digest. |
| Recorded outcome | Replaying stored evidence is allowed only with an explicit cached/recorded marker, bound to the same identity and digest. |

## Provider contract for a single-task observation (future)

If observation is ever added, it is one provider operation with its own contract,
not a `result()` variant and not a new method registered by this version. The
requirements below describe what such an operation must guarantee. The SSH
transport can satisfy them with a bounded read-only probe; a BYOC provider may
satisfy them only if its resident helper can answer without streaming the
archive — otherwise it answers `unsupported`.

| Proposed element | Requirement |
| --- | --- |
| Scope | Exactly one job identity per call, for the calling owner. No batch, no list, no "observe every live job" fan-out. |
| Input identity | The durable record's own facts — `owner_key`/root frame, `job_id`, `provider`, remote handle. A caller-supplied remote path, command or process id is never accepted. |
| Output | A normalized answer: observation `state`, `observed_at`, a bounded reason and at most the wrapper's status evidence. No file bytes, no manifest, no digest, no job-row write. |
| Read-only transport | The probe may run one bounded remote read (for example `kill -0`, reading `.rc` or `.phase`) but must never stream `out.tar.gz`, unpack an archive or write the workspace/data directory. |
| No resubmission | Observation cannot submit, retry, cancel or terminate; those remain separate explicit actions with their own permissions. |
| Capability declaration | A provider or transport either declares observation support with a read-only probe or answers `unsupported`. An undeclared or unverifiable capability is `unsupported`, never a best-effort harvest. |
| Bound and timeout | One bounded timeout per observation (the value is a later implementation decision). Expiry is `unknown`; there is no background retry, and repeated observations are independent bounded calls. |

## Observation state, time and timeout (future)

The response is additive: an observation `state`, an `observed_at` time and a
bounded `reason`. `updated_at` keeps its meaning (when the record last changed),
and unknown is never rendered as running, succeeded or failed.

| Proposed state | Meaning |
| --- | --- |
| `observed` | A provider round-trip answered at `observed_at`, for the exact identity above. |
| `recorded` | Replay of a stored terminal outcome; authoritative as history, but not a fresh observation and not stamped with a new observation time. |
| `unobserved` | No observation has been attempted; distinct from "not running" and from "no such job". |
| `unsupported` | The provider or transport cannot answer without harvesting, the capability is absent, or the feature is disabled. Manual retrieval remains available. |
| `unknown` | Attempted but no evidence: unreachable host, authentication failure, remote timeout, or unreadable wrapper state. The job may still be live. |
| `refused` | The caller may not observe this identity (owner/visibility) or the request is malformed; no existence leak beyond today's `not_found`. |
| `disabled` | The observation capability is switched off (its default). Old listing, refresh, cancel and result paths are unaffected. |

A future observation timeout bounds the observation call, not the job: expiry
yields `unknown` with the reason, the observer returns, and nothing is cancelled
or retried in the background. A late answer after the caller left updates no
cached state — an observation is not a subscription. Repeated observations are
allowed, bounded and side-effect free; there is no daemon poller, no auto-refresh
and no scheduler.

## Relationship with the C1 task listing (future)

C1 (plan §6 and appendix B.1) gives the task listing its own durable-record
contract. The C1 compliance pass may still adjust internals around this
document, so the relationship below is stated against the plan's contract —
`limit`/`state`/`cursor`, `tasks`/`live_count`/`polled`, and the additive
`total_live_count`/`has_more`/`next_cursor` — rather than against one
implementation detail.

| C1 listing fact | Relationship to observation |
| --- | --- |
| `GET …/compute/tasks` stays a durable-record read with `polled: false` and no remote action. | Observation never changes that response. It is a separate explicit action and cannot be reached from the GET path. |
| `live_count` is live jobs *on the page*; `total_live_count` is the same-owner total across all states. | Both remain durable-record counts. They are never computed from observations and never presented as remote truth. |
| Ordering is `created_at DESC, job_id DESC`; `has_more` and `next_cursor` walk that order. | Observation does not reorder, insert, remove or consume a cursor; pagination is unaffected. |
| `public_task.updated_at` is when the state was last established. | Observation's `observed_at` is a separate task-scoped time and is never written into `updated_at` in this version. |
| The record may already hold `unknown` as a live state. | The listing keeps showing the recorded `unknown` as live and not as failure; an observation returning `unknown` does not change the record. |
| A record read failure is 503 `compute_record_unavailable` (C1). | An observation failure is `unknown`/`unsupported`/timeout for the remote read. A client must not collapse "record unreadable" into "remote unknown" or the reverse. |
| C1's new fields are additive and optional for old clients. | Observation adds its own separate result when implemented; it does not replace, extend or reinterpret the C1 response shape. |

## Side-effect contract

- Observation writes no workspace or data-directory file, creates no
  `hpc/<job_id>/` tree, registers no Artifact version and runs no
  `ArtifactManager.capture`.
- It commits no terminal state, exit code, manifest, digest or `terminal_at`, and
  it appends no job-history event. If a later design wants an observation log,
  that is a separate storage decision with its own migration and retention
  review, not part of this contract.
- It does not submit, cancel, terminate a sandbox or touch a warm BYOC sandbox's
  `/work`; a future implementation must demonstrate that its probe command can
  only read the wrapper's state files.
- It does not change `result()` semantics. `result()` stays probe-plus-harvest;
  `_stored_terminal_result` keeps returning recorded evidence with
  `cached: True`; the refresh POST stays the explicit write path.
- It does not replace cancellation. Cancel stays a separate, confirm-gated
  action with `cancel_confirmed`, `cancel_indeterminate` (202) and
  `already_terminal` (409) outcomes, and an observation never changes them.

## Explicit retrieval stays separate

`POST …/refresh` keeps its current chain: owner-scoped manager, `result()`,
Artifact capture, Stage 11 receipt binding, `polled: true`, and the existing
`no_provider`, `refresh_failed` and `harvest_provenance_invalid` failures. The
native `compute_result` tool (`writes_files = True`) and the SDK's `.result()`
stay write-capable (harvest and terminal commit). The SDK's documented contract
— poll from a later cell until terminal, nothing harvests on its own — is
unchanged. Authentication, session visibility and owner scoping remain on every
path; observation introduces no new permission bypass and no new approval
surface. Closing the future observation capability must leave all of these
untouched.

## Capability shortage (future)

- The stable reason vocabulary should distinguish: no provider configured, the
  provider has no observation operation, the remote is unreachable, the caller
  is not the owner, the observation timed out, and observation is disabled.
- A UI without a supported observation shows "observation is not supported
  here" and keeps today's explicit refresh, rather than inventing a status.
- No fallback may call `result()` implicitly; a status that costs a harvest is
  an explicit user action.
- A provider that gains observation support only later must not require the old
  paths to change.

## Compatibility and non-goals

- **This version does not implement the contract.** No route, response field,
  poller, persisted column, provider operation, configuration switch or
  migration ships here; this document is a preparation record only.
- `result()` keeps its present semantics: it is still the probe-plus-harvest
  write path, `_stored_terminal_result` still replays recorded evidence, and
  nothing harvests without an explicit action.
- No background polling, no timer-driven network request, no auto-refresh and no
  subscription: observation would be a user-triggered, bounded call (INV-4).
- No new approval or permission surface. A future observation must reuse the
  existing session authentication, session visibility and owner scoping, and
  must not widen the provider's authority.
- Absence or disablement of the future capability leaves the listing, refresh,
  cancel, `result()` and `job_history()` untouched; old clients keep reading the
  old shapes, and the separate `openai4s/orchestration/` observation is not
  extended into this contract.

## Future local acceptance matrix

All cases below are **planned** and extend
[`tests/test_compute_durability.py`](../tests/test_compute_durability.py),
[`tests/test_compute_task_centre.py`](../tests/test_compute_task_centre.py) and
[`tests/test_compute_owner_and_harvest.py`](../tests/test_compute_owner_and_harvest.py).
Use synthetic data, a temporary data directory and a stubbed or local transport
at the provider boundary; real SSH/BYOC evidence must be recorded separately and
is never inferred from a stub.

| ID | Controlled case | Required observable result |
| --- | --- | --- |
| R01 | Network down or host unreachable during observation | State `unknown` with a bounded reason; zero workspace/data-dir writes; the durable row and listing are unchanged; a later observation may resolve it. |
| R02 | Remote probe finds no exit-code evidence (`NORC`) or unreadable phase | `unknown`, distinct from `failed`; no terminal commit, no cached terminal result created, retry stays possible. |
| R03 | Observation for another session's job id | The owner-scoped not-found refusal; zero remote calls for the foreign id; no existence confirmation and no data leak. |
| R04 | Observation concurrent with cancel or submit; cancel returning indeterminate | Observation changes no state and overrides no outcome; the confirmed/indeterminate/already-terminal cancel semantics are preserved. |
| R05 | Daemon restart around an observation | Rehydrate binds this owner's live rows; nothing is resubmitted; a lost remote handle yields `unsupported`/`unknown`; a stored terminal result replays as `recorded`. |
| R06 | Side-effect scan around one single-task observation | Workspace, data directory, Artifact versions, job rows and job events are unchanged; the provider received at most one bounded probe for exactly the requested job, with no harvest and no terminal write. |
| R07 | Provider without an observation capability, and the feature disabled | `unsupported`/`disabled` with a stable reason; explicit refresh and `result()` still work; no implicit harvest occurs. |
| R08 | Identity drift — a reused BYOC sandbox, a stale receipt, a mismatched provider | The answer is refused or `unknown`; it is never attributed to the earlier job, and no manifest or digest is written. |
| R09 | C1 listing before and after observations | `GET …/compute/tasks` still reports `polled: false`, unchanged `updated_at`, `live_count`/`total_live_count`/`has_more`/`next_cursor`; no observation time appears as a stored update and no cursor changes. |
| R10 | Explicit retrieval after observations | The refresh still harvests and captures Artifact versions; `result()` returns the same recorded terminal evidence with `cached: true`; observation created no Artifact. |
| R11 | Observation deadline expiry, with a remote that keeps answering slowly | Bounded `unknown` with a reason; no stranded handler/worker and no cancellation of the job; a second observation is a new bounded call. |
| R12 | Capability disabled and old-client compatibility | Old fields keep their shape and meaning; new fields are optional; with observation off, listing, refresh, cancel and result are unchanged and no migration is required. |

Completion of this preparation means the observable/unsupported split, the
identity binding, the provider contract, the observation-state vocabulary, the
C1 relationship, the side-effect contract and the cases above are agreed, and
current compute tests still pass with no behavior change. Runtime completion
requires executing R01–R12 on the real implementation, with real SSH/BYOC
observation where that environment exists; runs that cannot be performed are
recorded as `not_run`, never as passed. This document alone does not satisfy
that later gate.
