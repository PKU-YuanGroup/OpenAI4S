# Remote GPU compute

Model-weight-bound work runs its heavy step on a remote GPU, not the local kernel. There are two paths.

> **Where this fits.** `host.compute` is the **ComputeProvider** surface (jobs:
> stage → run → harvest). It is one of several platform-integration kinds —
> ComputeProvider, ModelEndpointProvider, LabProvider, Worker Runtime, and
> Transport — whose boundaries and implementation status are defined in
> [`docs/package-architecture.md`](package-architecture.md). Only the compute
> providers below (`byoc:*`, `ssh:*`) are implemented today (model endpoints
> are partial: the registry exists, the scoped inference kernel is not yet
> wired); SLURM/Kubernetes/Modal/lab providers are **future** and must not be
> assumed available.

## 1 · `host.compute` — general BYOC / SSH job dispatcher

A job is dispatched non-blocking (`create → submit_job → wait → result`): the daemon stages inputs, runs the job on the remote provider, and harvests `out.tar.gz` back into the workspace under `hpc/<job_id>/`. The harvest is bounded — the archive is rejected outright on path traversal, absolute paths, symlink/hardlink or device members, or a decompression bomb, because remote bytes are untrusted input even on the happy path. Two provider families are built in:

- **`ssh:<alias>`** — run jobs over an SSH connection to a machine you already have ([`skills/remote-compute-ssh`](../skills/remote-compute-ssh)).
- **`byoc:<id>`** — a bring-your-own-compute provider discovered from `skills/remote-compute-<id>/` (`provider.json` + `provider.py`).

The bundled **NVIDIA NIM** provider ([`skills/remote-compute-nvidia`](../skills/remote-compute-nvidia)) uses only the `docker` CLI (no SDK):

| form | needs | where the job runs |
|---|---|---|
| `self_hosted` | Docker + NVIDIA Container Toolkit + `NGC_API_KEY` | an `nvcr.io` NIM container on a local GPU (`--gpus all`) |
| `hosted` | Docker + an `nvapi-…` `NVIDIA_API_KEY` | the managed `integrate.api.nvidia.com` gateway (no local GPU) |

```python
c   = host.compute.create("byoc:nvidia", provider_params={"nvidia": {"mode": "hosted"}})
job = c.submit_job(intent="run esmfold2 on 1 seq", command="python run_esmfold.py ./seq.fasta",
                   inputs=[{"src": "seq.fasta"}], outputs=["*.pdb"], timeout_seconds=3600)
result = job.result()   # non-blocking once the compute_done notification arrives
```

The daemon forwards **only** the keys a provider declares in its `provider.json` `secret_env` into the job (over the helper's stdin) — never your whole environment.

### Job states

Terminal states are mutually exclusive and never optimistic:

| state | meaning |
|---|---|
| `done` | the job's exit code was read and was 0, and its outputs were harvested and verified |
| `failed` | the job's exit code was read and was non-zero |
| `timed_out` | a deadline or per-job timeout sentinel fired |
| `incomplete` | the job itself succeeded, but its outputs could not be verified (lost by the wrapper, a partial transfer, or a rejected archive) |
| `cancelled` | a cancel was delivered and confirmed |
| `unknown` | **the outcome could not be established** |

`unknown` is not a synonym for failure — the job may well have succeeded. It means there is no evidence either way (the host was unreachable, the remote process was killed without writing an exit code, a helper blew its deadline, or a `.phase` marker was unparseable). Reconcile it against the remote before re-submitting; a blind retry may duplicate work that already completed. Nothing resolves `unknown` to success by default.

### Durability

A remote job outlives the daemon — an `ssh:*` job keeps running under `nohup`, a
`byoc:*` sandbox keeps billing — so jobs are recorded in SQLite, not in process
memory. Each job's row is written **before** the submit is attempted; a row
written only on success would be missing for exactly the case that matters, when
the provider took the work and the response never came back. On acknowledgement
the provider's receipt (remote pid / sandbox id) is stored: evidence the job
exists out there, independent of anything this process chose to believe. Every
transition appends to a sequenced `compute_job_events` stream — a status says
where a job is, the stream says how it got there, which is what tells "never
submitted" from "submitted, response lost".

A restart rehydrates whatever was still live, so a recovered job can still be
polled, harvested, and cancelled, and still occupies its concurrency slot rather
than letting the session oversubscribe a provider that is still busy.

`host.compute.reconcile()` reports what came back:

```python
host.compute.reconcile()
# {'recovered': [{'job_id': 'job-…', 'provider': 'ssh:lab',
#                 'status': 'running', 'receipt': '31337', 'hint': …}], 'count': 1}
host.compute.job_history('job-…')   # the sequenced event stream
```

**Nothing is resubmitted automatically.** A job in `submitted` may or may not be
running remotely, and guessing wrong costs either a duplicate charge or a lost
result — so reconcile surfaces the job with its receipt and lets a poll resolve
it. Pass `idempotency_key` to `submit_job` to make a retry of the same logical
work safe: a second submit under a key that already has a job is refused with
`duplicate_request` rather than becoming a second remote job, and the key
survives a restart, which is precisely when a client retries.

### Confinement status (Prototype)

`openai4s_compute_provider` ships a confinement probe and an `expect_confined` mode, but **nothing on the host currently wraps the helper in an OS sandbox** or supplies the probe's netns anchor — so confinement is a designed boundary, not a built one, and `host.compute` remains Prototype. Do not read "the helper ran" as "the helper was confined".

`OPENAI4S_COMPUTE_CONFINEMENT` mirrors `OPENAI4S_KERNEL_SANDBOX`'s vocabulary:

- `auto` (default) — run unconfined; the posture is reported, never implied.
- `enforce` — refuse `byoc:*` ops outright, since a verified boundary cannot be established on this host. Fail closed rather than pretend.
- `off` — same as `auto` today; reserved for when a real boundary exists.

The confined helper that stages, runs, and harvests each job is the **worker runtime** package [`openai4s_compute_provider`](../openai4s_compute_provider) — shared by every `byoc:*` provider. Despite its name it is a worker runtime, not a provider registry; it is kept under that legacy name for import compatibility (see [`docs/package-architecture.md`](package-architecture.md)). Its import-time secret-scrubbing guarantees are documented in [`docs/security.md`](security.md).

## 2 · `host.fold` / `host.score_mutations` — purpose-built science services over SSH

- **`host.fold(seq)`** runs **real single-sequence Protenix (AlphaFold3-class) inference** on a GPU host (the in-repo runner is [`scripts/fold_remote.sh`](../scripts/fold_remote.sh)). It is single-sequence (no MSA) and returns a PDB structure with per-residue pLDDT. The reference host is an 8×A100-80GB box; a single fold uses one GPU.
- **`host.score_mutations(...)`** runs **real ESM masked-marginal** variant scoring.

Both are governed by a strict **no-fabrication policy** — when no host is configured they *refuse and error* rather than invent a structure or scores — and each result records a reproducibility-provenance snapshot into its artifact.

### Auto-provisioning

You don't have to hand-configure model services. Register an SSH GPU host in **Settings → Compute**; when a GPU/protein task needs a service that isn't set up yet, the agent calls the built-in **`REMOTE_GPU_PROVISIONER`** specialist, which SSHes in, installs the real wrappers, **verifies** them, and only then **registers** the capability (no fake registration — registration only succeeds after the remote service is confirmed). Inspect the registry with `host.remote_gpu_status()`. Prefer **project-scoped** permission rules for remote work (e.g. `ssh my-gpu-host *`).

### Config

| env var | for |
|---|---|
| `NVIDIA_API_KEY` / `NGC_API_KEY` | NVIDIA NIM (`hosted` / `self_hosted`) |
| `OPENAI4S_FOLD_SSH` · `OPENAI4S_FOLD_SCRIPT` · `OPENAI4S_FOLD_JOBS_DIR` | `host.fold` over SSH |
| `OPENAI4S_ESM_JOBS_DIR` | `host.score_mutations` scratch dir |

SSH auth stays in your `~/.ssh/config` / ssh-agent — the registry stores no secrets.
