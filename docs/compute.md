---
title: Remote compute
description: Current Partial/Prototype support boundary for general remote jobs and purpose-built SSH scientific services.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
implementation_status: Partial/Prototype
status: current
audience: [operators, contributors, users]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Remote compute

Remote compute is **Partial/Prototype**. OpenAI4S contains real transport, provider, registry, approval, and SSH service code, but it is not yet a durable scheduler or a production-grade untrusted execution boundary. Validate every provider and remote wrapper against the exact deployment before relying on it for scientific results.

There are two distinct APIs:

| API | Purpose | Current status |
|---|---|---|
| `host.compute` | General command/job dispatch to `ssh:<alias>` or `byoc:<id>` | **Prototype**: useful scaffolding, process-local job state, provider-specific gaps |
| `host.fold` / `host.score_mutations` | Synchronous purpose-built scientific wrappers selected from a verified SSH capability registry | **Partial**: real no-fabrication path, but deployment-specific provisioning and validation |

Do not describe one path as an implementation of the other. `host.fold` does not submit through `host.compute`, and a generic `host.compute` job does not inherit the folding service's result validation.

## `host.compute`: general job dispatch

The worker-facing [compute SDK](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/sdk/compute.py) sends `compute_*` Host RPC calls to a session's [ComputeManager](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/compute/manager.py). Provider families are named:

- `ssh:<alias>` for an SSH name already configured for the daemon account;
- `byoc:<id>` for a provider discovered from `skills/remote-compute-<id>/provider.json` plus `provider.py`.

The repository currently bundles the SSH recipe and one BYOC provider, `byoc:nvidia`. The NVIDIA provider supports provider-defined Docker/NIM modes, subject to local Docker/GPU or managed-service prerequisites and credentials. Existence in the catalog is not evidence that those external prerequisites work.

A representative API shape is:

```python
compute = host.compute.create(
    "byoc:nvidia",
    provider_params={"nvidia": {"mode": "hosted"}},
)
job = compute.submit_job(
    intent="run a validated inference script",
    command="bash run_inference.sh",
    inputs=[{"src": "run_inference.sh"}, {"src": "input.json"}],
    outputs=["out/*"],
    timeout_seconds=900,
)

status = job.result()
```

Approval coverage is narrower than the SDK surface. `compute_submit` is
approval-gated. Result polling, cancellation, and provider close do not request
a second approval, and the legacy direct SSH/SCP helpers do not enter the Tool
permission gate. All remain risk-bearing external operations and are audited or
routed through Host-side code, but that is not equivalent to approval. Keep
submission targets narrow and restrict the daemon account's SSH identity.

### Current prototype limits

- Job records, concurrency counters, and BYOC sandbox handles live in the session's in-process `ComputeManager`. They are not stored in SQLite and do not survive daemon/dispatcher replacement as scheduler jobs.
- The manager does not currently implement the background polling/notification loop described by older SDK comments. Callers must poll `job.result()`; do not depend on `compute_done` notification delivery.
- The generic SSH submit path starts a remote script but currently does not automatically stage declared `inputs`, persist a reliable remote exit code, or harvest declared scientific `outputs`; its result path retrieves logs and leaves the remote work directory in place. Use explicit, verified transfer operations and inspect remote state.
- The BYOC path has provider-specific staging and harvest behavior, but it is not an adversarial-provider boundary. Enable only reviewed provider code and trusted output sources.
- Provider discovery proves that metadata and a Python shim exist. It does not probe live credentials, capacity, images, endpoint compatibility, scientific model versions, or output correctness.
- Default tests use fakes and remain offline. Docker, GPU, SSH, external API, and large-output behavior require opt-in live validation.
- Local Gateway `/compute/jobs` is a separate host-side job surface and is not the `host.compute` provider sandbox.

Because the state is not durable, record the provider, remote work directory/sandbox identity, command revision, input hashes, and expected outputs outside the live Job object before launching expensive work. A Workbench Stop button or kernel loss is not a remote scheduler cancellation; explicitly cancel or clean up the provider resource.

## BYOC credential boundary

The host reads only secret environment names declared by a provider's `provider.json` and sends their values to the helper over stdin/fd 3, not as the job process environment. The helper applies baseline secret-name/prefix scrubbing before importing provider code and provider-declared prefix scrubbing before reading the credential.

This is a name-based heuristic. A secret stored under an unrecognized variable name can remain visible at provider import time. Provider modules are executable trusted extensions, not data-only manifests. See [Security architecture](security.md#remote-compute-boundaries).

The built-in NVIDIA provider declares `NGC_API_KEY` and `NVIDIA_API_KEY`. Do not place these values in source, job commands, provider parameters, logs, or artifacts.

## `host.fold` and `host.score_mutations`

These services use the [remote capability registry](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/compute/registry.py) and run a registered wrapper directly over SSH through [RemoteScienceService](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/host/remote_science.py).

### `host.fold`

`host.fold(sequence, ...)` accepts one protein sequence, sanitizes it to the supported amino-acid alphabet, caps the current path at 1,200 residues, and invokes a registered `fold` script. The expected wrapper returns a structured manifest and base64-encoded PDB, with optional confidence and provenance blocks. The current contract is single-sequence and does not claim an MSA workflow.

### `host.score_mutations`

`host.score_mutations(sequence, ...)` accepts a protein sequence up to 1,024 residues and invokes a registered `score_mutations` wrapper. The expected result contains a structured summary and encoded CSV score table.

### No-fabrication contract

Both services return a single-key `{"error": ...}` result when no capable host exists, SSH fails, the wrapper times out, or required structured output is absent/unparseable. They do not replace missing results with random coordinates, heuristic scores, or fabricated model output.

This contract proves absence of intentional fallback fabrication in the Host service. It does not independently validate that a remote wrapper ran the claimed model, used the claimed weights, or produced scientifically valid output.

## Host and capability registration

Settings can register an SSH alias that already exists in the daemon account's `~/.ssh/config`. OpenAI4S stores host metadata and capability records in `remote_compute.json`; private keys and ssh-agent credentials remain outside the registry.

`host.register_remote_capability(...)` verifies a structured `path_exists` or `executable_exists` probe over SSH before recording a capability. A successful probe means the path or binary was present at that moment. It is not:

- a cryptographic attestation of wrapper contents;
- a model-weight/version verification;
- a scientific golden test;
- an ongoing health check;
- authorization for unrestricted use of the remote account.

The built-in `REMOTE_GPU_PROVISIONER` is an LLM-driven specialist that inspects a host, runs approved shell steps, and registers only after a probe succeeds. It is not a deterministic installer, and successful delegation must not be equated with a supported, reproducible production service. Operators should provision wrappers through reviewed infrastructure, pin versions and hashes, run known-answer tests, and then register the verified path.

## Provenance and result handling

The purpose-built services can attach a remote environment/provenance JSON block supplied by the wrapper to the producing Cell's Artifact environment snapshot. Missing or malformed remote provenance is non-fatal. Treat it as remote-supplied metadata, not attestation.

For any remote path, capture at least:

- provider/SSH alias and remote directory or sandbox identity;
- command/wrapper revision and model/weight identifier;
- input Artifact version IDs and content hashes;
- container/environment lock information;
- start/end time, exit status, stdout/stderr tail, and output hashes;
- explicit confirmation that expected output files exist and parse;
- remote retention and cleanup decision.

Promote results to versioned Artifacts only after validation. Harvested files under `hpc/` are ordinary instance data and must be covered by backup/retention policy.

## Configuration

| Setting | Purpose |
|---|---|
| `~/.ssh/config` and ssh-agent/key files | SSH aliases and authentication; external to OpenAI4S backup unless separately captured |
| `OPENAI4S_INSTALL_ID` | Optional stable BYOC owner tag; set explicitly in managed deployments |
| `NVIDIA_API_KEY` / `NGC_API_KEY` | Built-in NVIDIA provider credentials |
| `OPENAI4S_FOLD_SSH` | Compatibility seed for an initial folding SSH host when the registry is empty |
| `OPENAI4S_FOLD_SCRIPT` | Compatibility/default fold wrapper path |
| `OPENAI4S_FOLD_JOBS_DIR` | Remote folding work root |
| `OPENAI4S_ESM_JOBS_DIR` | Remote mutation-scoring work root |

Configuration does not make the capability live. Probe SSH non-interactively as the daemon account, test the exact wrapper with known inputs, verify result parsing, and exercise cancellation/cleanup.

## Readiness checklist

Before using remote compute for important work:

1. Use a dedicated remote account and least-privilege SSH key.
2. Review provider and wrapper code; pin images, dependencies, model weights, and hashes.
3. Verify network destinations and credential forwarding.
4. Run a small known-answer job and compare output to an independent expectation.
5. Test timeout, non-zero exit, malformed output, lost SSH connection, cancellation, and daemon restart.
6. Confirm which inputs/outputs are transferred automatically for the selected provider; do not generalize from another provider family.
7. Record remote cleanup and cost ownership.
8. Keep a manual recovery path using the recorded remote identifier because `host.compute` Job objects are not durable.

Until these checks pass for a specific deployment, report remote compute as unavailable or experimental rather than implying that the presence of a UI card or Skill guarantees execution.
