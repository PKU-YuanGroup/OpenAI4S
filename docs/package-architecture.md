# Package architecture — platform provider kinds

> Status: **architecture reference.** This document describes the *intended*
> boundaries between the platform-integration concepts in OpenAI4S and marks
> exactly which of them are implemented today versus planned. It is a naming
> and taxonomy contract, **not** a description of a completed refactor. No code
> has been moved or renamed to match the target layout in
> [`docs/refactor-plan.md`](refactor-plan.md); the paths below that do not
> exist yet are labelled **future**.

OpenAI4S has one strong core — a pure-stdlib Code-as-Action runtime (see
[`docs/architecture.md`](architecture.md)). Around that core sit several
*platform integration* concepts that today are spread across
`openai4s/compute/`, `openai4s_compute_provider/`, `skills/remote-compute-*`,
and `skills/using-model-endpoint`. Because those concepts are entangled, new
contributors keep adding provider/transport/endpoint code in the wrong place
(usually `skills/`). This document draws the boundaries so future additions
land correctly.

The five concepts, and where each stands today:

| Concept | What it is | Implemented today? |
|---|---|---|
| **ComputeProvider** | Launches a *job* (stage inputs → run → harvest outputs) on some compute backend. | **Yes** — `byoc:<id>` (Docker/NIM) and `ssh:<alias>`. |
| **ModelEndpointProvider** | Serves *inference* from an already-running model behind an HTTP `BASE_URL`. Request/response, no job lifecycle. | **Partially** — the `host.endpoints.*` registry (register/status/probe) is implemented; calls are plain HTTP from the normal agent kernel. The scoped inference kernel is **designed, not wired** (see below). |
| **LabProvider** | Drives physical lab instruments / automation hardware. | **No — future.** No lab code ships today. |
| **Worker Runtime** | The *inside-the-sandbox* process contract: how staged code actually runs in a confined remote/container process, plus its hardening (env scrub, redaction, harvest). | **Yes** — `openai4s_compute_provider` (kept under this legacy name for compatibility). |
| **Transport** | How the host *reaches* a backend: `ssh <alias>`, the local `docker` CLI, an HTTP gateway. | **Yes**, per provider — not a standalone pluggable layer. |

Keeping these distinct matters because they have **different trust boundaries
and lifecycles**: a compute job spends the user's allocation behind an approval
modal and harvests files; a model endpoint is a plain HTTP client with no job
to harvest; a lab run would touch physical hardware; the worker runtime is the
one component that must scrub secrets and confine execution regardless of which
of the others launched it.

## ComputeProvider

A ComputeProvider dispatches a **job**: `create → submit_job → wait → result`,
with inputs staged in and `out.tar.gz` harvested back into
`hpc/<job_id>/`. This is the `host.compute.*` surface. The host-side manager is
[`openai4s/compute/manager.py`](../openai4s/compute/manager.py); the SSH host
registry is [`openai4s/compute/registry.py`](../openai4s/compute/registry.py).

Two provider *families* are implemented today, both selected by a provider
string:

- **`ssh:<alias>`** — run a job over an SSH connection to a host you already
  have in `~/.ssh/config`. Enabled by the
  [`remote-compute-ssh`](../skills/remote-compute-ssh) skill being present.
  Transport is plain `ssh <alias>`; the registry stores **no secrets** (auth
  stays in your ssh config / agent).
- **`byoc:<id>`** — a bring-your-own-compute provider discovered from
  `skills/remote-compute-<id>/` (`provider.json` + `provider.py`). The bundled
  example is [`remote-compute-nvidia`](../skills/remote-compute-nvidia) (NVIDIA
  NIM over the `docker` CLI, no SDK).

### Not implemented — future ComputeProvider kinds

The target layout in [`docs/refactor-plan.md`](refactor-plan.md) reserves
provider slots under a future `openai4s/platforms/compute/providers/`
(`slurm.py`, `modal.py`, `kubernetes.py`, …). **None of these exist today.**
There is:

- **No dedicated SLURM provider.** You can already run SLURM *through* the
  `ssh:` family by SSHing to a login node and calling `sbatch`/`squeue`
  yourself inside `command=` — that is a usage pattern of the SSH transport,
  not a distinct `slurm:` provider kind.
- **No Modal provider.**
- **No Kubernetes provider.**

Do not add these as skills. When they are built, they belong under the future
`platforms/compute/providers/` boundary as first-class provider kinds, not as
science recipes in `skills/`.

## ModelEndpointProvider

A ModelEndpointProvider serves **inference from an already-running model**. It
is request/response over HTTP with **no job lifecycle** — no submit, no
harvest.

**What is implemented today** is the endpoint *registry*: the
`host.endpoints.*` surface (`list` / `free_port` / `register` / `status` /
`probe`, the `_m_endpoints_*` handlers in
[`openai4s/host_dispatch.py`](../openai4s/host_dispatch.py)). It stores an
endpoint's URL, optional start/stop/live scripts (script changes require an
approval card), and a credential *name* (never a value). The agent then calls
the endpoint with plain HTTP **from the normal agent kernel** — there is no
dedicated inference kernel today.

**What is designed but not wired:** a *scoped inference kernel* per registered
endpoint — a Python REPL whose network egress is limited to exactly that
endpoint, reached via a `compute_provider({'provider': '<slug>', 'code': '…'})`
dispatch with `BASE_URL` preloaded. The
[`using-model-endpoint`](../skills/using-model-endpoint) skill and its
`provider.py` (`InferProvider`, built on the compute-provider worker runtime)
document and prototype this mechanism, but **no code path dispatches to it**:
there is no `compute_provider` callable in the codebase, the compute manager's
provider discovery only scans `skills/remote-compute-*` directories (so the
`infer` provider is never discovered), and nothing preloads `BASE_URL` into any
kernel or scopes kernel egress per endpoint. Do not rely on the scoped kernel
until a later PR wires it.

Even so, this is a genuinely different *kind* from ComputeProvider: an endpoint
has no files to harvest and no allocation to spend, so it should not be
conflated with a compute job. Managed (daemon-owned start/stop) endpoints are a
further variant; their lifecycle is out of scope for this document.

## LabProvider — future

A LabProvider would drive physical lab instruments or automation hardware, with
its own trust boundary and lifecycle. **No lab code ships in OpenAI4S today.**
The `platforms/lab/` slot in the target layout is a placeholder. Any mention of
"lab" in the roadmap is aspirational; do not document lab behavior as available.

## Worker Runtime — `openai4s_compute_provider`

The [`openai4s_compute_provider`](../openai4s_compute_provider) package is
**not a provider registry despite its name.** It is the **worker runtime**: the
shared, stdlib-only contract for *how staged code runs inside a confined
remote/container process*, plus the hardening every confined provider process
inherits. It owns:

- the `ByocProvider` / `ExecResult` contract (`_protocol.py`);
- the fd-3 control channel, auth handshake, and stdout scrubber (`_channel.py`);
- the resident process lifecycle — prologue, oneshot, and repl op loops
  (`_resident.py`);
- wire limits, sandbox paths, and error kinds (`_constants.py`);
- **import-time secret scrubbing** (see below and
  [`docs/security.md`](security.md)).

It is deliberately kept under the legacy name `openai4s_compute_provider` for
**backward compatibility** — existing imports, the `byoc:` skills, and packaging
metadata all reference it. The refactor plan
([`docs/refactor-plan.md`](refactor-plan.md), section E, **Option 4**) chooses
to keep this package import-compatible and add an `openai4s_worker_runtime`
alias that re-exports the same symbols. **That alias now exists** (PR 09):
`openai4s_worker_runtime/__init__.py` is a pure re-export of the public
contract — every public symbol is the identical object under both names, the
private submodules stay in the primary package, and the runnable entrypoint
remains `python -m openai4s_compute_provider`. New code may prefer the alias
name; every existing `openai4s_compute_provider` import keeps working. The
legacy name says "compute provider"; the reality is a worker runtime shared by
every provider kind that stages and runs code.

Because the worker runtime is the layer that actually executes untrusted-ish
provider shims, its secret-scrubbing guarantees are described in one place and
must stay consistent with them:
[`docs/security.md` → BYOC provider import-time secret scrubbing](security.md).
In brief, scrubbing is **name-based and two-staged** — a provider-agnostic
baseline (`scrub_secret_env()`) runs in `__main__.py` *before* `provider.py` is
imported, and the resident prologue re-scrubs with the loaded provider's own
declared prefixes before the credential is read over a side channel. A secret
stored in a variable whose name matches neither the credential-shape regex nor a
known prefix is **not** scrubbed; this is a heuristic, not an OS sandbox.

## Transport

Transport is *how the host reaches a backend*, and today it is **per provider**,
not a standalone pluggable layer:

- `ssh:<alias>` transports over `ssh <alias>` (the alias's auth lives in the
  user's ssh config / agent).
- `byoc:nvidia` transports over the local `docker` CLI (and, for the `hosted`
  form, an HTTPS call to `integrate.api.nvidia.com`).
- Registered model endpoints are called with plain HTTP from the normal agent
  kernel, using the URL stored in the `host.endpoints.*` registry. (The
  designed-but-unwired scoped inference kernel would route through the sandbox
  HTTP proxy to a preloaded `BASE_URL`; see the ModelEndpointProvider section.)

The target layout imagines a shared transport/protocol boundary, but there is
no generic transport abstraction in the code today — each provider knows its own
transport.

## Legacy compatibility guarantees

- `openai4s_compute_provider` stays importable under that exact name; the worker
  runtime is described truthfully here and in `docs/security.md`, and is only
  *named* a "compute provider" for compatibility. The `openai4s_worker_runtime`
  alias re-exports the same public symbols under the accurate name;
  `tests/test_worker_runtime_alias.py` pins the two names to the identical
  contract.
- The provider strings `byoc:<id>` and `ssh:<alias>` are stable; a future
  provider-`kind` taxonomy will be introduced *alongside* them via
  compatibility adapters, not by breaking these strings.
- `skills/remote-compute-*` continue to work as-is. Trusted platform lifecycle
  (scheduler, transport, secrets, job lifecycle) is expected to move to a future
  `openai4s/platforms/*` boundary later, with the old skill entrypoints
  forwarding — but **no such move has happened**, so these skills remain the
  live integration points today.

## For contributors — where new integrations go

- A new way to **run a job** (a new cloud/scheduler/container backend) is a
  **ComputeProvider**, not a skill. Until `platforms/compute/providers/` exists,
  a `byoc:<id>` provider (`provider.json` + `provider.py`) is the supported
  extension point.
- A new **model to call over HTTP** is a **ModelEndpointProvider** — register an
  endpoint; the science of *what to send* belongs in a runbook skill.
- A new **science recipe** (what to compute and why) is a **skill**, and must
  not embed secrets, SSH credentials, scheduler/job-lifecycle logic, or cloud
  SDK side effects at import time (see
  [`docs/refactor-plan.md`](refactor-plan.md), section D).

## Next steps

Subsequent roadmap PRs (see [`docs/refactor-plan.md`](refactor-plan.md),
section G) build on this taxonomy:

- **PR 09** (done) — added the `openai4s_worker_runtime` alias package that
  re-exports the worker runtime, keeping `openai4s_compute_provider` primary
  and import-compatible (compatibility pinned by
  `tests/test_worker_runtime_alias.py`).
- Later PRs — introduce explicit provider `kind` fields (`compute`,
  `model_endpoint`, `lab`) and move trusted provider lifecycle under
  `openai4s/platforms/*` with the legacy skill/provider entrypoints forwarding.
