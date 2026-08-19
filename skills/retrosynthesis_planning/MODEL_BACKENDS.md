# Optional Retrosynthesis Model Backends

[中文说明](MODEL_BACKENDS_zh.md)

This document describes the optional external-model boundary for the retrosynthesis planning Skill. The OpenAI4S side remains stdlib-only. Heavy model packages, checkpoints, CUDA libraries and model-specific dependencies stay in a separate Python or conda environment and communicate with OpenAI4S through one versioned JSON request and one JSON response.

The first implementation supports single-step inference with RetroChimera and the model wrappers exposed by Syntheseus. It does not replace AiZynthFinder multi-step planning, and it does not treat a model score as an experimental success probability.

## Scope

The external backend is intended for three uses:

- generating additional single-step precursor proposals;
- comparing proposals from models with different inductive biases;
- recording model and checkpoint provenance before a proposal is used in route review.

Multi-step Syntheseus search, forward-model validation, model-consensus ranking and interactive subtree replanning are planned follow-ups rather than hidden behavior in this first adapter.

## Architecture

```text
OpenAI4S retrosynthesis Skill
        |
        | one versioned JSON request on stdin
        v
isolated syntheseus_worker.py process
        |
        | optional imports and model inference
        v
RetroChimera or Syntheseus model environment
        |
        | one versioned JSON response on stdout
        v
schema validation, provenance checks and Harness replay
```

Stdout is reserved for one JSON object. Before it handles a request the worker moves descriptor 1 onto stderr and keeps a private duplicate for the response, so a native library that writes to stdout directly — PyTorch, DGL, CUDA and RDKit all do — does not corrupt the protocol. Rebinding `sys.stdout` alone would not be enough, because those writes never pass through it. The duplicate is closed in any forked child, so a model that forks without exec cannot hold the host's pipe open past its own exit.

Three limits on that, stated rather than implied. The swap declines when there is no usable stderr to move stdout onto, and the worker then answers on the unprotected stdout — no better off than before, but visibly so. It happens inside the worker, so it cannot cover bytes written before the interpreter reaches it: a startup banner from a `sitecustomize` on an inherited `PYTHONPATH` still corrupts the response, as it does for `openai4s/kernel/worker.py`. And because model stdout now arrives on stderr, which the host quotes back in a `nonzero_exit` message, that message is path-scrubbed before it is raised.

The host never uses `shell=True`, applies request and response size limits, enforces a timeout, verifies the response `request_id`, and rejects unknown response fields.

## Supported model classes

| Family | Model names accepted by the worker | Intended role | Dependency note |
| --- | --- | --- | --- |
| RetroChimera ensemble | `RetroChimera` | Recommended first external second-opinion model | Install the separate `retrochimera` package and Syntheseus interface dependencies. |
| RetroChimera components | `RetroChimeraEdit`, `RetroChimeraDeNovo` | Diagnose whether graph-edit and sequence-generation components agree | Use the same checkpoint family and record the exact component in the manifest. |
| Template and graph models | `GLN`, `Graph2Edits`, `LocalRetro`, `MEGAN`, `MHNreact` | Add structurally different proposal mechanisms | Each wrapper may require its own Syntheseus optional dependency group. |
| Sequence and retrieval models | `Chemformer`, `RootAligned`, `RetroKNN` | Add sequence-aligned or retrieval-based proposals | Install only the dependency group and checkpoint actually being used. |

The adapter deliberately caps `num_results` at 10. Lower-ranked predictions are not presented as equally reliable alternatives, and downstream code must preserve rank and raw score type rather than silently converting every score into a common probability.

## Trust and download policy

"Isolated" here means a dependency boundary, not a security one. The worker is an ordinary subprocess: it inherits the caller's environment, runs under no OS sandbox, and has no egress control of its own. It keeps PyTorch, CUDA and model-specific packages out of the OpenAI4S core process; it does not contain the model code. Treat a checkpoint and its wrapper as code you are choosing to run.

Automatic checkpoint downloading is disabled by default. Calling `single_step(...)` without `model_dir` raises before the external process is launched unless `allow_model_download=True` was set explicitly.

The safer production pattern is:

1. obtain a checkpoint through an approved process;
2. review the checkpoint and training-data license;
3. compute a SHA-256 checksum;
4. create a path-free public model manifest;
5. pass both the local checkpoint directory and manifest to the adapter.

The local `model_dir` is sent only to the isolated worker. It is not copied into the normalized result, dashboard, Harness tape or model manifest. This avoids leaking a workstation path into a public artifact.

Model-reported metadata is filtered the same way before it leaves the worker. Keys named `*path*` or `*directory*` are dropped, and any remaining string — value or key — that *begins* with an absolute path, a home-relative path, a UNC share or a `file://` URL is replaced with `<redacted-path>`. Error messages are scrubbed more aggressively, anywhere in the string, because a missing checkpoint surfaces as exception text carrying the caller's `model_dir`.

Two boundaries are worth stating plainly rather than implying. Metadata values are matched only at the start of the string, so a path mentioned mid-sentence in a wrapper's free-text note is not masked: an unanchored match cannot distinguish `kcal/mol` or the bond directions in `F/C=C/F` from a directory, and mangling chemistry to catch a prose mention is the worse trade. And redaction runs inside the worker, so it cannot help with bytes written before the worker starts.

## Installation

Create an isolated environment rather than adding model packages to the OpenAI4S core environment. A reference setup for the versions used while developing this adapter is:

```bash
conda create -n openai4s-retro python=3.11 -y
conda activate openai4s-retro
pip install syntheseus==0.7.2 retrochimera==1.2.0
```

Other Syntheseus model wrappers have model-specific optional dependencies. Follow the upstream installation instructions for the selected model rather than installing every model family by default.

The adapter does not add `syntheseus`, `retrochimera`, PyTorch or CUDA to `pyproject.toml`. The worker reports installed package versions at runtime, and a missing or incompatible package is returned as a structured backend error.

### Reproducible RetroChimera checkpoint setup

`model_deployment.py` records the public Pistachio, USPTO-FULL and USPTO-50K RetroChimera archives and their upstream byte counts, MD5 values, DOI records and MIT license. Listing the registry is offline:

```bash
python skills/retrosynthesis_planning/model_deployment.py list
```

Downloading is disabled unless the operator supplies `--allow-network`. Run this command in an operator shell or the isolated model environment, subject to the deployment's normal network policy:

```bash
python skills/retrosynthesis_planning/model_deployment.py download \
  uspto50k /models/retrochimera/retrochimera_uspto50k.zip \
  --allow-network
```

The smaller USPTO-50K archive is useful for an installation smoke test but is not a substitute for the broader main checkpoint. Upstream describes Pistachio as the main and most powerful released checkpoint. Install either archive only after validation:

```bash
python skills/retrosynthesis_planning/model_deployment.py extract \
  uspto50k \
  /models/retrochimera/retrochimera_uspto50k.zip \
  /models/retrochimera/uspto50k \
  --manifest /models/retrochimera/uspto50k-manifest.json
```

The command validates the reviewed byte count and MD5, computes SHA-256, rejects absolute paths, traversal, backslashes and symlinks in the ZIP, bounds member count and expanded size, and publishes the model directory only after extraction succeeds. It refuses to replace an existing model directory. The generated manifest is path-free and can be passed directly to `SyntheseusBackend`.

## Model manifest

A model manifest is public provenance, not an environment configuration file. It must not contain a local checkpoint path, credential, private dataset location or internal experiment name.

```json
{
  "schema_version": 1,
  "provider": "Microsoft Research",
  "model": "RetroChimera",
  "model_version": "1.2.0",
  "checkpoint_id": "reviewed-pistachio-checkpoint",
  "checkpoint_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "training_dataset": "Pistachio",
  "code_license": "MIT",
  "checkpoint_license": "MIT",
  "source_url": "https://doi.org/10.6084/m9.figshare.30591107.v1",
  "metadata": {
    "reviewed_by": "replace-with-public-review-role"
  }
}
```

`provenance_status` is `complete` only when a checkpoint SHA-256 is present, the training dataset is identified, and both code and checkpoint licenses are explicit rather than `unknown`, `unspecified` or `review-required`. A manifest fingerprint is computed from canonical JSON so a changed manifest is visible even when the human-readable checkpoint ID stays the same. The worker echoes the manifest back untouched — redaction applies to model-reported metadata, never to the operator's own document, because filtering it would mean the published fingerprint no longer reproduces from the reviewed file. `SyntheseusBackend` compares the fingerprint it gets back against the manifest it sent and raises `manifest_mismatch` if they differ, so a worker cannot quietly substitute a provenance record nobody approved.

## Usage

```python
from retrosynthesis_planning.external_backends import SyntheseusBackend

backend = SyntheseusBackend(
    model="RetroChimera",
    model_dir="/models/retrochimera/checkpoint",
    manifest="/models/retrochimera/model-manifest.json",
    python_command=(
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "openai4s-retro",
        "python",
    ),
    timeout_seconds=600,
    env={
        "WANDB_MODE": "offline",
        "SYNTHESEUS_CACHE_DIR": "/models/syntheseus-cache",
    },
)
```

`env` adds only the listed values to the inherited worker environment. It is intended for model-specific cache and offline-mode controls, not credentials; keep secrets in the normal credential broker.

`--no-capture-output` is required, not cosmetic: without it `conda run` does not
forward stdin, the worker reads an empty request, and every call comes back as
an `invalid_json` error response instead of a result.

```python

capabilities = backend.capabilities()
result = backend.single_step(
    "CC(=O)Oc1ccccc1C(=O)O",
    num_results=5,
)
```

The result preserves:

- model name and runtime package versions;
- ordered reactant proposals and reaction SMILES;
- the original score field and score type when available;
- model metadata that can be represented as JSON, with filesystem paths removed;
- the public model manifest and its fingerprint;
- warnings when checkpoint provenance is incomplete;
- a scientific disclaimer that prevents a model score being described as yield or success probability.

## Wire contract

The wire schema is versioned independently from any model package. The worker currently supports `capabilities` and `single_step` operations.

A successful single-step response contains `target_smiles`, `model`, ordered `predictions`, `model_manifest`, `runtime`, `warnings` and `elapsed_seconds`. A failed request contains a structured `error` with `code`, `message` and `retryable`.

Expected error codes include:

- `checkpoint_required` when automatic download is disabled and no model directory was supplied;
- `dependency_missing` when the selected optional package is absent;
- `dependency_incompatible` when the installed package does not export the expected class;
- `unsupported_model` or `unsupported_operation` for a request outside the versioned contract;
- `inference_failed` for a model-side failure that was caught and serialized;
- host-side `timeout`, `nonzero_exit`, `invalid_json` and `response_too_large` execution errors.

A structured model error is a valid backend response and can be handled as one failed provider in a larger ensemble. A process crash, invalid stdout or request-ID mismatch is a protocol failure and raises on the host side.

## Harness and verification

The default PR suite does not download model weights. `harness/evals/retrosynthesis_backend_cases.json` contains public-safe synthetic response tapes, and `harness/evals/retrosynthesis_backends.py` sends them through the same production response normalizer used for a real worker result.

The replay report includes:

- case accuracy;
- expected success and error-code agreement;
- prediction counts;
- complete-provenance rate for successful cases;
- scored-prediction coverage;
- a canonical SHA-256 digest for every normalized response.

Run the focused contracts with:

```bash
uv run pytest tests/test_harness_contract.py
uv run python -m harness.cli run --tier pr --offline
```

A future opt-in model canary may load a small reviewed checkpoint set, but it must carry an external/GPU marker and must not become a requirement for the default offline PR suite.

## Scientific interpretation

RetroChimera and other learned retrosynthesis models can produce chemically implausible or out-of-distribution proposals. Agreement between models is evidence of computational consistency, not proof that a transformation works. A high raw model score is not automatically calibrated across model families.

Before a proposal is promoted into an executable route, review should include deterministic structure checks, reaction-center inspection, forward or round-trip validation where available, source-backed reaction precedent, inventory verification, safety review and an independent chemistry expert decision.

The adapter therefore returns proposals and provenance. It does not generate a synthetic yield, hide model disagreement or label a prediction as experimentally verified.

## Planned follow-ups

The next compatible layers are:

- a normalized multi-backend candidate bundle and reciprocal-rank consensus;
- forward-model round-trip and stereochemistry-aware validation;
- weakest-step and shared-failure analysis across route alternatives;
- PaRoutes-style offline route benchmarking and opt-in model canaries;
- an interactive route DAG showing model votes, reaction centers, evidence grade and review actions;
- multi-step Syntheseus search as a separate capability with its own inventory and search manifest.

Those changes should remain separate PRs so the external process boundary and provenance contract can be reviewed before model outputs influence route ranking or the workbench UI.
