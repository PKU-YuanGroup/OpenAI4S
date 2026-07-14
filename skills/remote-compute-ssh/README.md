# Remote Compute SSH Skill

This progressive-disclosure recipe describes the control-plane workflow for already configured SSH/SLURM compute: discover host details, stage files, submit with approval, wait for notification, harvest outputs, and record reusable host knowledge. It does not itself register an SSH provider or grant access to a host.

Availability depends on user configuration, credentials, scheduler/allocation state, remote software, and approvals. Submitting jobs can consume real resources; the recipe requires validation and explicit intent rather than claiming success from a queued command.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Runbook for `host.compute`/control-kernel usage, compute detail discovery, environment activation, file staging, direct/SLURM job submission, notification, harvesting, cancellation/recovery, and host-note updates. |

## Direct subdirectories

None.
