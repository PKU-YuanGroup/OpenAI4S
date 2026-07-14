# Remote-job shell templates

[中文说明](README_zh.md)

These templates are copied into the per-job staging tree by [`ComputeManager`](../manager.py). They run inside a BYOC provider sandbox; they are not daemon startup scripts and are not used by the direct SSH compatibility path.

## Files

| File | Responsibility |
|---|---|
| [`run.sh.tmpl`](run.sh.tmpl) | Minimal job entry point: enables strict Bash mode, changes to the staged work directory, and substitutes the submitted command at `{{COMMAND}}`. |
| [`wrapper.sh.tmpl`](wrapper.sh.tmpl) | Supervises `run.sh` in its own process group, separates stdout/stderr, enforces job and sandbox-deadline termination, reaps descendants, writes phase/deadline markers, and unconditionally attempts to archive `out/` plus logs as `out.tar.gz`. |

## Subdirectories

There are no tracked child directories here.

## Runtime contract

- User code must place desired result files under `./out/`; an empty directory only produces a warning.
- Deadline-control environment values are read and made read-only before `.job_env` is sourced, preventing job-supplied variables from widening those limits.
- The workload runs in a distinct session/process group. TERM, grace, then KILL handling tries to stop descendants before staging results.
- `.phase` records `done:<rc>:<wall>` or `harvest_failed:<rc>:<wall>`. Deadline/job-timeout sentinel ordering is part of the host/provider classification contract.
- Output staging is attempted even after timeout or non-zero workload exit so logs and partial results can still be harvested.

## Security and portability boundaries

- `{{COMMAND}}` is intentionally executable job content, not shell-escaped data. Safety depends on the provider sandbox and the authority granted at submission.
- The wrapper assumes a Linux/GNU-style environment with Bash, `setsid`, `timeout`, `tar`, process groups, and `/proc/<pid>/stat`; it is not a portable local shell wrapper.
- `.job_env` is sourced inside the already-authorized sandbox. Control variables are protected, but arbitrary job environment values remain available to the workload.
- Marker files are defensive coordination signals, not cryptographic attestations. The wrapper narrows forgery windows; the host must still treat remote outputs as untrusted.
- The template produces a per-job archive only. Host-side extraction, path validation, durable registration, Artifact versioning, and scientific validation are separate responsibilities and may fail independently.

## Related documentation

- [Compute backend](../README.md)
- [Remote compute](../../../docs/compute.md)
- [Worker runtime](../../../openai4s_compute_provider/README.md)
