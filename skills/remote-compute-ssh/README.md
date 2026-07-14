# Remote Compute SSH Skill

Dispatching work to an SSH/SLURM host the user has already configured: find out what the host actually offers, stage the files, submit and let the approval modal do its work, park until the notification arrives, harvest the outputs, then write down what you learned about the host so the next session starts from it. Every submit puts a modal in front of the user and, once approved, spends their allocation — a string of failed submits costs their attention and their compute — so the recipe is shaped around landing the first one. It registers no SSH provider and grants access to no host.

Whether any of it works depends on the user's configuration, their credentials, the scheduler and allocation state, what software the remote actually has, and the approvals. Submitting a job spends real resources, so the recipe insists on validating the result and on explicit intent, and refuses to treat a queued command as a success.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook for `host.compute` and the control kernel: why these calls belong in the `repl` tool rather than the `python` tool, how to read the compute details doc and how much discovery is left, finding a working environment activation, staging inputs, submitting directly or through SLURM, waiting for the notification, harvesting, cancellation and recovery, and updating the host notes afterwards. |
