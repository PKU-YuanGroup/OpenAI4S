# Optional Retrosynthesis Model Backends

[中文说明](MODEL_BACKENDS_zh.md)

This document describes the optional external-model boundary for the retrosynthesis planning Skill. The OpenAI4S side remains stdlib-only. Heavy model packages, checkpoints, CUDA libraries and model-specific dependencies stay in a separate Python or conda environment and communicate with OpenAI4S through one versioned JSON request and one JSON response.

The same boundary now covers AiZynthFinder, RXNMapper, ReactionT5v2-forward,
ReactionT5v2-yield, and Parrot through `reaction_model_backends.py` and
`reaction_model_worker.py`. `reaction_model_deployment.py` is the authoritative
environment/artifact registry. It pins package versions and upstream revisions,
generates reviewable install/download commands, snapshots every artifact file,
and verifies those snapshots before inference. Network commands are printed but
never executed implicitly.

| Capability | Frozen identity | Required external artifact |
| --- | --- | --- |
| AiZynthFinder | 4.4.1 / release commit `9859f5b…` | Complete `download_public_data` policy/template/filter/stock/config snapshot |
| RXNMapper | 0.4.3 / tag commit `640d9dd…` | Reviewed PyPI wheel plus embedded model, wheel SHA recorded in the registry |
| ReactionT5v2 forward | HF revision `9331140…` | Complete local HF snapshot; inference is `local_files_only` |
| ReactionT5v2 yield | HF revision `f0658bf…` | Complete local HF snapshot; inference is `local_files_only` |
| Parrot | repository commit `0fb2325…` | Repository, config, label dictionaries, and checkpoint snapshot after terms review |

Parrot and AiZynthFinder public-data artifacts remain `review-required` until
their separately downloaded files and terms have been audited. A code license
does not silently license a dataset or checkpoint.

The original boundary supports single-step inference with RetroChimera and the
model wrappers exposed by Syntheseus. The reaction-model sibling now implements
AiZynthFinder multi-step search, mapping, forward prediction, yield estimation,
and condition recommendation. No model score is treated as an experimental
success probability.

## Verified deployment status

| Backend | Engineering status | Scientific-use status |
| --- | --- | --- |
| AiZynthFinder 4.4.1 | Direct `plan_routes` worker and Scenario 2 conversion are implemented and contract-tested. The isolated environment is external to git. | Live search still requires an approved, hashed policy/template/filter/stock snapshot; upstream calls it public but does not state one artifact-wide license in the downloader. |
| RXNMapper 0.4.3 | Pinned isolated environment, wheel hash, manifest, and real mapping smoke test pass. | Ready for mapping benchmarks subject to normal domain checks. |
| ReactionT5v2-forward | Pinned HF snapshot `9331140...` and real CPU model-card product canary pass. | Usable as a bounded forward/round-trip signal, not feasibility proof. |
| ReactionT5v2-yield | Pinned HF snapshot loads; upstream preprocessing is reproduced. Its published canary expected about 19.1666 but returned 65.924858. | Quarantined: protocol testing only until resolved and independently validated. |
| Parrot | Source is pinned at `0fb2325...`; the official CSV beam adapter is contract-tested. | Checkpoint inference is blocked because the Google Drive artifacts have no separate explicit terms in the official downloader. |

## Scope

The external backends are intended for these bounded uses:

- generating additional single-step precursor proposals;
- searching multi-step routes against a declared stock;
- mapping atoms and extracting reaction-centre evidence;
- predicting forward products for round-trip diagnostics;
- adapting complete Parrot condition beams after artifact approval;
- exercising the yield wire protocol while its current checkpoint is quarantined;
- comparing proposals from models with different inductive biases;
- recording model and checkpoint provenance before a proposal is used in route review.

Multi-step Syntheseus search, model-consensus ranking, and interactive subtree replanning remain separate capabilities rather than hidden behavior in one adapter.

## Architecture

```text
OpenAI4S retrosynthesis Skill
        |
        | one versioned JSON request on stdin
        v
isolated syntheseus_worker.py or reaction_model_worker.py
        |
        | optional imports and model inference
        v
reviewed model-specific environment and local artifacts
        |
        | one versioned JSON response on stdout
        v
schema validation, provenance checks and Harness replay
```

Stdout is reserved for one JSON object. Before it handles a request the worker moves descriptor 1 onto stderr and keeps a private duplicate for the response, so a native library that writes to stdout directly — PyTorch, DGL, CUDA and RDKit all do — does not corrupt the protocol. Rebinding `sys.stdout` alone would not be enough, because those writes never pass through it. The duplicate is closed in any forked child, so a model that forks without exec cannot hold the host's pipe open past its own exit.

Three limits on that, stated rather than implied. The swap declines when there is no usable stderr to move stdout onto, and the worker then answers on the unprotected stdout — no better off than before, but visibly so. It happens inside the worker, so it cannot cover bytes written before the interpreter reaches it: a startup banner from a `sitecustomize` on an inherited `PYTHONPATH` still corrupts the response, as it does for `openai4s/kernel/worker.py`. And because model stdout now arrives on stderr, which the host quotes back in a `nonzero_exit` message, that message is path-scrubbed before it is raised.

The host never uses `shell=True`, applies request and response size limits, enforces a timeout, verifies the response `request_id`, and rejects unknown response fields.

## Supported single-step Syntheseus model classes

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

The USPTO-50K checkpoint uses the optional Graphium architecture. Install
`retrochimera[graphium]==1.2.0` instead of the plain package before loading that
variant; the Pistachio and USPTO-FULL paths do not require the extra.

Other Syntheseus model wrappers have model-specific optional dependencies. Follow the upstream installation instructions for the selected model rather than installing every model family by default.

The adapter does not add `syntheseus`, `retrochimera`, PyTorch or CUDA to `pyproject.toml`. The worker reports installed package versions at runtime, and a missing or incompatible package is returned as a structured backend error.

### Reproducible RetroChimera checkpoint setup

`model_deployment.py` records the public Pistachio, USPTO-FULL and USPTO-50K RetroChimera archives and their upstream byte counts, MD5 values, DOI records and MIT license. Listing the registry is offline:

```bash
python -m skills.retrosynthesis_planning.model_deployment list
```

Downloading is disabled unless the caller explicitly opts in, and it runs through OpenAI4S `host.web_download` so every redirect is checked by the egress allowlist and SSRF guard. Run this in an OpenAI4S Python cell, with a destination inside the session workspace:

```python
from pathlib import Path

from retrosynthesis_planning.model_deployment import (
    checkpoint_spec,
    download_checkpoint,
)

workspace = Path.cwd().resolve()
archive = workspace / "models" / "retrochimera" / "retrochimera_uspto50k.zip"
spec = checkpoint_spec("uspto50k")
download_checkpoint(
    spec,
    archive,
    allow_network=True,
    web_download=host.web_download,
)
```

`host` is the singleton already injected into the cell; it is not an importable
module. Passing the capability explicitly also keeps the helper testable and
prevents a standalone script from silently opening the network itself.

`host.web_download` streams the response to an atomic temporary file while enforcing the byte ceiling and computing SHA-256; it does not accumulate a multi-gigabyte checkpoint in daemon memory. An operator may instead use the deployment environment's approved streaming downloader, then run the offline `verify` command below before extraction. The standalone module deliberately does not open the network itself.

The smaller USPTO-50K archive is useful for an installation smoke test but is not a substitute for the broader main checkpoint. Upstream describes Pistachio as the main and most powerful released checkpoint. Install either archive only after validation:

```bash
CHECKPOINT_ROOT="$PWD/models/retrochimera"

python -m skills.retrosynthesis_planning.model_deployment verify \
  uspto50k "$CHECKPOINT_ROOT/retrochimera_uspto50k.zip"

python -m skills.retrosynthesis_planning.model_deployment extract \
  uspto50k \
  "$CHECKPOINT_ROOT/retrochimera_uspto50k.zip" \
  "$CHECKPOINT_ROOT/uspto50k" \
  --manifest "$CHECKPOINT_ROOT/uspto50k/model-manifest.json"
```

Run that block from the same session workspace root used by the download Cell.
`$PWD/models/retrochimera` is therefore the one writable checkpoint root for
download, verification, extraction, manifest creation, and inference.

The command copies no more than the reviewed archive size to a private snapshot while validating byte count and MD5 and computing SHA-256, then extracts only that verified snapshot. It rejects non-regular and oversized sources, absolute paths, traversal, backslashes, Windows drive-relative/alternate-stream/device names and symlinks, and bounds member count and expanded size. A requested manifest must live inside the new model directory; it is written in private staging so the manifest and extracted files become visible together in one atomic directory publication. The command refuses a model directory that exists when extraction starts. Callers must serialize extraction attempts for the same destination: the existence check and final POSIX directory rename are not a cross-process lock, and a concurrently created empty directory can otherwise be replaced. The generated manifest is path-free and can be passed directly to `SyntheseusBackend`.

Downloads and standalone manifest writes bind publication to the verified file
inode when the workspace filesystem supports hard links. On filesystems such as
exFAT or some SMB mounts that reject hard links, they retain private staging,
pre/post byte verification, and atomic rename, but callers must also serialize
writes to the same destination.

## Model manifest

A model manifest is public provenance, not an environment configuration file. It must not contain a local checkpoint path, credential, private dataset location or internal experiment name.

```json
{
  "schema_version": 1,
  "provider": "Microsoft Research",
  "model": "RetroChimera",
  "model_version": "1.2.0",
  "checkpoint_id": "reviewed-uspto50k-checkpoint",
  "checkpoint_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "training_dataset": "USPTO-50K",
  "code_license": "MIT",
  "checkpoint_license": "MIT",
  "source_url": "https://doi.org/10.6084/m9.figshare.30601718.v1",
  "metadata": {
    "reviewed_by": "replace-with-public-review-role"
  }
}
```

`provenance_status` is `complete` only when a checkpoint SHA-256 is present, the training dataset is identified, both code and checkpoint licenses are explicit rather than `unknown`, `unspecified` or `review-required`, and the digest is not explicitly scoped only to a source archive. The deployment helper records `checkpoint_sha256_scope: source_archive` and `runtime_integrity: unverified`: its digest proves which reviewed ZIP was installed, not that the mutable extracted directory still contains those bytes when inference runs, so its status remains `incomplete`. Writing `runtime_integrity: verified` into a manifest cannot upgrade that status; a real host-side directory verifier would be required. A manifest fingerprint is computed from canonical JSON so a changed manifest is visible even when the human-readable checkpoint ID stays the same. The worker echoes the manifest back untouched — redaction applies to model-reported metadata, never to the operator's own document, because filtering it would mean the published fingerprint no longer reproduces from the reviewed file. `SyntheseusBackend` compares the fingerprint it gets back against the manifest it sent and raises `manifest_mismatch` if they differ, so a worker cannot quietly substitute a provenance record nobody approved.

## Usage

```python
from pathlib import Path

from retrosynthesis_planning.external_backends import SyntheseusBackend

workspace = Path.cwd().resolve()
model_dir = workspace / "models" / "retrochimera" / "uspto50k"
manifest = model_dir / "model-manifest.json"
cache_dir = workspace / "models" / "syntheseus-cache"
cache_dir.mkdir(parents=True, exist_ok=True)

backend = SyntheseusBackend(
    model="RetroChimera",
    model_dir=model_dir,
    manifest=manifest,
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
        "SYNTHESEUS_CACHE_DIR": str(cache_dir),
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
- weakest-step and shared-failure analysis across route alternatives;
- PaRoutes-style offline route benchmarking and opt-in model canaries;
- an interactive route DAG showing model votes, reaction centers, evidence grade and review actions;
- multi-step Syntheseus search as a separate capability with its own inventory and search manifest.

Those changes should remain separate PRs so the external process boundary and provenance contract can be reviewed before model outputs influence route ranking or the workbench UI.
