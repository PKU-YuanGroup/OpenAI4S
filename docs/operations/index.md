---
title: Operations overview
description: Supported deployment shape, operating boundaries, and the runbook index for an OpenAI4S workbench.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Operations overview

OpenAI4S is a **single-user scientific workbench for a local or trusted host**. It is not a public, multi-tenant application server. The supported operating shape is one trusted operator, one dedicated operating-system account, one data directory, and a daemon bound to loopback. Remote users reach that loopback listener through an SSH tunnel or a trusted VPN.

The public documentation site is a different system. `openai4s.org/docs/` is a static VitePress build containing no Workbench process, model key, SQLite database, or research artifact. It may be served publicly. Do not reverse-proxy a Workbench under the public documentation origin.

## Supported operating shape

| Concern | Supported baseline | Do not assume |
|---|---|---|
| Audience | One trusted operator and their local research sessions | Tenant isolation, per-user authorization, quotas, or administrative roles |
| Network | `127.0.0.1:8760`; SSH tunnel or trusted VPN for remote access | Safe direct Internet exposure |
| Process | One foreground daemon, normally supervised by the OS | Active/active replicas or concurrent daemons sharing a data directory |
| Storage | One local `OPENAI4S_DATA_DIR`, including SQLite and file state | Network-filesystem coordination or database-only backup completeness |
| Execution | Lazy Python/R subprocesses under the daemon account | A hostile-code multi-tenant sandbox |
| Availability | Operator-managed restart and restore | Zero-downtime upgrades or automatic disaster recovery |

The daemon is a singleton keyed by its pidfile. All server threads share one `Store` connection, while scientific work runs in child processes. Keep each installation's data directory private to one daemon account and never point two live daemons at the same directory.

## Runbook map

- [Deployment](deployment.md) covers installation, service supervision, loopback remote access, static documentation publication, upgrade, and rollback.
- [Data, backup, and restore](data-management.md) defines what must be backed up and how to produce a consistent instance snapshot.
- [Security hardening](security-hardening.md) is the operator checklist for accounts, permissions, network exposure, sandbox posture, approvals, and monitoring.
- [Security architecture](../security.md) explains which controls enforce, annotate, fail open, or degrade.
- [Implementation status](../reference/implementation-status.md) separates implemented contracts from partial and prototype surfaces.
- [Remote compute](../compute.md) records the narrower, currently experimental support boundary for `host.compute`, `host.fold`, and `host.score_mutations`.

## First-boot checklist

1. Create a dedicated, non-login or otherwise minimally privileged OS user.
2. Create a local data directory owned only by that user and start the service with `umask 077`.
3. Keep `OPENAI4S_HOST=127.0.0.1`. Use `OPENAI4S_KERNEL_SANDBOX=enforce` when an unavailable sandbox must stop execution instead of degrading.
4. Install and test the release as the service user. The daemon may install missing scientific packages in its interpreter in the background; wait for the environment status to settle before declaring the service ready.
5. Configure model credentials without placing them in the repository. Treat the SQLite database, `.env`, logs, session exports, and artifacts as sensitive.
6. Run the offline test gate and a real loopback browser smoke test before accepting work.
7. Take a stopped, whole-data-directory backup and test a restore before the first upgrade.

## Daily checks

Use the CLI from the same environment and with the same `OPENAI4S_DATA_DIR` as the service:

```bash
openai4s status
curl --fail --silent http://127.0.0.1:8760/health
```

Also inspect:

- disk space and inode use for the data directory;
- ownership and absence of group/other access;
- failed background scientific-package installation in the environment status;
- the Workbench security projection after Python and R have actually started, because sandbox state is `not_started` before a worker self-test runs;
- pending approvals and unexpected standing permission rules;
- incomplete remote jobs, which are not a durable scheduler queue.

## Incident priorities

If exposure, credential leakage, corrupted state, or unexpected code execution is suspected:

1. Stop the daemon and remote-compute activity.
2. Preserve a read-only copy of the data directory and service logs.
3. Revoke model, search, connector, cloud, and remote-compute credentials outside OpenAI4S.
4. Review permissions, SSH access, recent Actions/Cells, and artifact versions.
5. Restore only after matching the backup to a known code release and validating it in an isolated loopback instance.

Do not delete the original data directory during recovery. OpenAI4S migrations run forward when a database is opened; a rollback therefore needs both a compatible code revision and the pre-upgrade data snapshot.
