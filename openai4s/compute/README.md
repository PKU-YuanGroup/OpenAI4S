# Remote-compute host backend

[中文说明](README_zh.md)

The host side of the evolving `host.compute` job surface lives here, together with the separate registry that the purpose-built remote-science services use to find a real GPU host. Heavy work leaves the machine one of two ways: through a discovered `byoc:<id>` provider, or over an `ssh:<alias>` connection the user already has. This is orchestration and transport code. There is no scheduler here, no GPU runtime, and no scientific model.

## Where this fits

The Python [`host.compute` SDK](../sdk/compute.py) turns every call into a `compute_<operation>` Host RPC. [`HostDispatcher`](../host_dispatch.py) creates one [`ComputeManager`](manager.py) per session on first use and maps `ComputeError` into a structured soft failure. The native `compute_submit`, `compute_result`, `compute_cancel` and `compute_close` tools expose a bounded slice of that control plane; the richer SDK compatibility calls reach the same manager.

For `byoc:*`, the manager looks for provider shims under `skills/remote-compute-<id>/`, stages a job archive, and runs the confined [`openai4s_compute_provider`](../../openai4s_compute_provider) helper with credentials on stdin. For `ssh:*`, it shells out to local `ssh`/`scp` against a user-configured alias. Harvested files land below the configured data directory, in `hpc/<job_id>/`.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Exports the two names the rest of the host needs: `ComputeManager` and the structured `ComputeError`. |
| [`manager.py`](manager.py) | Both transports. It discovers BYOC provider Skills, routes `byoc:*` and `ssh:*`, refuses a submission once the session's in-memory concurrency limit is reached, stages inputs and the job templates, and tracks live jobs and warm sandboxes through poll, cancel, close and harvest. The credential is picked out by the environment-variable names the provider declares, and it reaches the helper on stdin rather than through the environment. The helper's environment is otherwise the daemon's own, minus every name beginning `NGC_`, `NVIDIA_` or `HF_`. |
| [`registry.py`](registry.py) | Remembers which SSH host aliases exist, which one is the default, and what `fold`/`score_mutations`-style capability metadata has been provisioned on each, written atomically to `<data_dir>/remote_compute.json`. Native registration probes the host before it records verification; a host seeded from the legacy environment variable may sit there unverified. It stores no SSH private keys and no provider tokens. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`templates/`](templates/) | Shell templates staged into a BYOC job. They run the submitted command, handle the time and deadline limits, and package output and logs for harvest. See its [README](templates/README.md). |

## Current lifecycle

1. `submit` validates the provider family and checks the manager's session-local concurrency count.
2. A BYOC submission creates or reuses a provider sandbox, builds `in.tar.gz` from the wrapper, the command and the inputs, and calls the helper's create and submit operations. An SSH submission creates a remote work directory and starts `run.sh` under `nohup`.
3. `result` polls the exact in-memory job. On the BYOC path the helper's wait stages `out.tar.gz`, which the manager extracts under `hpc/<job_id>/`; the SSH compatibility path copies the logs back and leaves its work directory on the remote host.
4. `cancel` signals the remote process or terminates the BYOC sandbox. `close` releases a known provider handle and marks the named live handles closed.

## Persistence, approval, and maturity boundaries

- **Job records are durable; warm-sandbox handles are not.** A job row is written *before* the submit is attempted and carries the provider receipt, so a restarted manager rehydrates every job that may still be consuming a remote resource, keeps it in the concurrency count, and can still poll or cancel it. `reconcile()` reports those jobs and deliberately never resubmits — a job in flight may or may not be running, and guessing wrong costs either a duplicate charge or a lost result. What is still in-memory only: the warm byoc sandbox handle per provider, so a restart cannot reuse a warm container (the job that container is running is still recoverable through its receipt). [`registry.py`](registry.py) persists the specialized SSH capability catalogue.
- **There is no background poller.** `result()` is what probes the remote and harvests; a job nobody polls is never harvested.
- Native `compute_submit` is approval-gated. Harvesting a result, cancelling, and closing deliberately do not ask a second time for a job that was already authorized. The richer direct `compute_ssh`/`compute_scp` compatibility methods are wider than that bounded native-tool gate, and approval of the latter must not be read as approval of them.
- BYOC confinement comes from the provider runtime and the provider shim together. Measure it, do not assume it. Credentials are selected by declared environment-variable name and sent on the helper's auth input, so a secret sitting in a name nobody declared can slip past name-based scrubbing.
- The current SSH job path is basic on purpose: bookkeeping is local memory, the remote directory is left behind, declared output patterns are not fully harvested, and the terminal exit status it reports is not a durable scheduler-grade contract.
- Harvested bytes, SQLite metadata, remote provider state, and the running scientific kernel are not in one transaction. A partial stage or harvest, or a process crash, can leave one layer ahead of another.
- The native registration path probes before it writes `verified_at`; the legacy `OPENAI4S_FOLD_SSH` seed and caller-supplied metadata may never have been checked. Resolving a capability says nothing about whether the host answers right now, so [`host/remote_science.py`](../host/remote_science.py) must check and fail honestly when the remote service is unavailable.
- Provider discovery only sees installed Skill directories that have both a `provider.json` and a `provider.py`. No SLURM, no Kubernetes, no general cluster scheduler is implemented here.

## Related documentation

- [Remote compute](../../docs/compute.md)
- [Security model](../../docs/security.md)
- [Package boundaries](../../docs/package-architecture.md)
- [Worker runtime](../../openai4s_compute_provider/README.md)

- [`safe_archive.py`](safe_archive.py) — enumerate-then-extract for a harvest that arrives from a machine we do not control: traversal, absolute paths, links, device nodes, and decompression bombs are refused before a byte is written.
