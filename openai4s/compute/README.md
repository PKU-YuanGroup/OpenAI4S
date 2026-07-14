# Remote-compute host backend

[中文说明](README_zh.md)

This directory is the host-side backend for the evolving `host.compute` job surface and the separate registry used by purpose-built remote-science services. Heavy work runs through either a discovered `byoc:<id>` provider or an existing `ssh:<alias>` connection; this package is orchestration/transport code, not a scheduler, GPU runtime, or scientific model implementation.

## Place in the architecture

The Python [`host.compute` SDK](../sdk/compute.py) emits `compute_<operation>` Host RPCs. [`HostDispatcher`](../host_dispatch.py) lazily creates one [`ComputeManager`](manager.py) for the session and maps `ComputeError` into a structured soft failure. Native `compute_submit`, `compute_result`, `compute_cancel`, and `compute_close` tools expose a bounded control-plane subset; richer SDK compatibility calls still reach the same manager.

For `byoc:*`, the manager discovers provider shims under `skills/remote-compute-<id>/`, stages a job archive, and invokes the confined [`openai4s_compute_provider`](../../openai4s_compute_provider) helper with credentials on stdin. For `ssh:*`, it calls local `ssh`/`scp` against a user-configured alias. Harvested files are placed below the configured data directory's `hpc/<job_id>/` tree.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Exports the host backend's `ComputeManager` and structured `ComputeError`. |
| [`manager.py`](manager.py) | Discovers BYOC provider Skills, routes `byoc:*` and `ssh:*`, enforces an in-memory session concurrency limit, stages input/templates, supplies only provider-declared credentials to the helper, tracks live jobs/sandboxes, polls/cancels/closes them, and harvests outputs. |
| [`registry.py`](registry.py) | Atomically stores SSH host aliases, default selection, and `fold`/`score_mutations`-style capability metadata in `<data_dir>/remote_compute.json`; native registration probes before marking verification, while legacy environment seeding may remain unverified. It stores no SSH private keys or provider tokens. |

## Subdirectories

| Directory | Responsibility |
|---|---|
| [`templates/`](templates/) | Shell templates staged into a BYOC job to run the submitted command, enforce time/deadline handling, and package output/logs for harvest. See its [README](templates/README.md). |

## Current lifecycle

1. `submit` validates the provider family and the manager's session-local concurrency count.
2. A BYOC submission creates/reuses a provider sandbox, builds `in.tar.gz` from the wrapper, command, and inputs, and calls the helper's create/submit operations. An SSH submission creates a remote work directory and starts `run.sh` with `nohup`.
3. `result` polls the exact in-memory job. BYOC wait stages `out.tar.gz`, which the manager extracts under `hpc/<job_id>/`; the SSH compatibility path copies logs and leaves its work directory remote.
4. `cancel` signals the remote process or terminates the BYOC sandbox; `close` releases a known provider handle and marks named live handles closed.

## Persistence, approval, and maturity boundaries

- **Prototype status:** `ComputeManager` job records, concurrency limits, and warm-sandbox handles are in memory. A daemon/manager restart cannot attach to those records through this implementation, even if remote work or harvested files still exist. [`registry.py`](registry.py) persists only the specialized SSH capability catalogue.
- Native `compute_submit` is approval-gated. Result harvesting, cancel, and close intentionally do not request a second approval for an already-authorized exact job. The richer direct `compute_ssh`/`compute_scp` compatibility methods are not equivalent to that bounded native-tool gate and must not be treated as newly approved authority.
- BYOC confinement is enforced by the provider runtime/provider combination and must be measured, not assumed. Credentials are selected by declared environment-variable names and sent over the helper's auth input; unknown secret names can escape name-based scrubbing.
- The current SSH job path is intentionally basic: job bookkeeping is local memory, the remote directory is left behind, declared output patterns are not fully harvested, and terminal exit-status reporting is not a durable scheduler-grade contract.
- Harvested bytes, SQLite metadata, remote provider state, and the running scientific kernel do not share one transaction. Partial staging/harvest or a process crash can leave one layer ahead of another.
- The native registration path probes before recording `verified_at`; the legacy `OPENAI4S_FOLD_SSH` seed and caller-supplied metadata may be unverified. Resolution does not prove current reachability, so [`host/remote_science.py`](../host/remote_science.py) must check and fail honestly when the remote service is unavailable.
- Provider discovery is limited to installed Skill directories with both `provider.json` and `provider.py`. No SLURM, Kubernetes, or general cluster scheduler is implemented here.

## Related documentation

- [Remote compute](../../docs/compute.md)
- [Security model](../../docs/security.md)
- [Package boundaries](../../docs/package-architecture.md)
- [Worker runtime](../../openai4s_compute_provider/README.md)
