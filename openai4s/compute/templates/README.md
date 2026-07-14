# Remote-job shell templates

[中文说明](README_zh.md)

[`ComputeManager`](../manager.py) copies these two templates into the staging tree it builds for each job, and they run inside a BYOC provider sandbox. They are not daemon startup scripts, and the direct SSH compatibility path never touches them.

## Files

| File | Responsibility |
| --- | --- |
| [`run.sh.tmpl`](run.sh.tmpl) | The job entry point, kept deliberately small: strict Bash mode, a change into the staged work directory, then the submitted command substituted at `{{COMMAND}}`. |
| [`wrapper.sh.tmpl`](wrapper.sh.tmpl) | Supervises `run.sh` in a process group of its own. It keeps stdout and stderr apart, enforces both the job timeout and the sandbox deadline, reaps surviving descendants, writes the phase and deadline markers, and always tries to archive `out/` plus the logs as `out.tar.gz`. |

## Runtime contract

- Results have to be written under `./out/`. An empty `out/` is only a warning.
- The deadline-control environment values are read and made read-only before `.job_env` is sourced, so a job cannot widen those limits through its own variables.
- The workload gets its own session and process group. TERM, then a grace period, then KILL, and the wrapper tries to stop the descendants before results are staged.
- `.phase` records either `done:<rc>:<wall>` or `harvest_failed:<rc>:<wall>`. The order in which the deadline and job-timeout sentinels are written is part of the contract the host and provider use to classify how a job ended.
- Output staging is attempted even after a timeout or a non-zero workload exit, so logs and partial results can still be harvested.

## Security and portability boundaries

- `{{COMMAND}}` is executable job content on purpose, not shell-escaped data. What keeps it safe is the provider sandbox and the authority granted at submission.
- The wrapper assumes a Linux/GNU-style environment with Bash, `setsid`, `timeout`, `tar`, process groups, and `/proc/<pid>/stat`. It is not a portable local shell wrapper.
- `.job_env` is sourced inside a sandbox that is already authorized. The control variables are protected, but every other job environment value stays available to the workload.
- Marker files are defensive coordination signals, not cryptographic attestations. The wrapper narrows the window in which they could be forged; the host still has to treat remote output as untrusted.
- All the template produces is one per-job archive. Extraction on the host, path validation, durable registration, Artifact versioning, and scientific validation are separate responsibilities and can each fail on their own.

## Related documentation

- [Compute backend](../README.md)
- [Remote compute](../../../docs/compute.md)
- [Worker runtime](../../../openai4s_compute_provider/README.md)
