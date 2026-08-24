# Auditable protein-design MCP server

[中文说明](README_zh.md)

This package provides nine atomic protein-design tools over the stdio MCP
transport. The server and orchestration code use only the Python standard
library; RFdiffusion, ProteinMPNN, ColabFold, PyRosetta, ESM-2 and OpenMM run
in separately configured environments and are never imported by OpenAI4S
core.

## What is real, and what must be provisioned

This is a real MCP server, not a schema-only example: it performs the MCP
`initialize`, `tools/list` and `tools/call` exchange over stdio, launches the
configured backend processes, verifies their inputs and outputs, and returns
structured terminal results. OpenAI4S starts it through the same persisted
connector manager used for other custom MCP servers.

It is not a bundled model runtime. Merely placing this package in the source
tree does not install RFdiffusion, ProteinMPNN, ColabFold, PyRosetta, ESM-2,
OpenMM, checkpoints or GPU drivers. Register it in **Customize → Connectors**
and bring up the backend settings below. Agent-facing calls bind the default
path root to the current session workspace, so target files and staged assets
are not interpreted relative to the daemon checkout. The
offline default tests exercise the real MCP process and command construction
against fake scientific executables; successful execution against each real
GPU backend remains an operator deployment acceptance check.

## Connector setup

In **Customize → Connectors**, add **Protein Design** from the
connector directory. This explicitly enables the capability but starts no
process yet: OpenAI4S launches the MCP server lazily on the first tool-discovery
or tool-call request. The equivalent custom stdio command uses the Python
interpreter from the OpenAI4S installation followed by:

```text
-m openai4s.mcp_servers.protein_design
```

Agent path binds `OPENAI4S_PROTEIN_DESIGN_ROOT` to the current session
workspace. An operator may explicitly set another root in connector settings.
All user-supplied paths are resolved under that root and escapes are rejected.

Configure immutable backend revisions with these variables:

- `OPENAI4S_RFDIFFUSION_REVISION`
- `OPENAI4S_PROTEINMPNN_REVISION`
- `OPENAI4S_COLABFOLD_REVISION`
- `OPENAI4S_PYROSETTA_REVISION`
- `OPENAI4S_ESM2_REVISION`
- `OPENAI4S_OPENMM_REVISION`

Commands are JSON string arrays, not shell snippets:

- `OPENAI4S_RFDIFFUSION_COMMAND` or `OPENAI4S_RFDIFFUSION_PATH` plus
  `OPENAI4S_RFDIFFUSION_PYTHON`;
- `OPENAI4S_PROTEINMPNN_COMMAND` or `OPENAI4S_PROTEINMPNN_PATH` plus
  `OPENAI4S_PROTEINMPNN_PYTHON`;
- `OPENAI4S_COLABFOLD_COMMAND`;
- `OPENAI4S_PYROSETTA_PYTHON`, `OPENAI4S_ESM2_PYTHON`, and
  `OPENAI4S_OPENMM_PYTHON` for the optional-dependency worker.

Blind complex prediction additionally requires
`OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX`, a JSON array that visibly creates a
networkless execution boundary, such as a configured bubblewrap prefix with
`--unshare-net`. Environment-only offline flags are not accepted as proof of
network isolation.

RFdiffusion, ProteinMPNN and ESM-2 calls supply a checkpoint path and expected
SHA-256. If no path was supplied, the Agent first asks whether the user already
has the file; `stage_model_asset` imports an approved local path, while a user
without the file goes through the normal approved download path. The connector
never silently downloads weights inside a scientific call. The bundled Agent
runtime admits a `run_mode=formal` call only after the same live MCP process
successfully ran `run_mode=canary` with the same tool, backend revision,
checkpoint digest and execution target. ColabFold prediction instead accepts
a JSON bundle manifest whose relative `data_dir` and `files` list contain every
model-data path plus SHA-256; the server rejects symlinks and unlisted files,
then verifies the manifest and complete model-data tree before startup.

## Tool boundary

| Tool | Contract |
| --- | --- |
| `generate_backbone` | One RFdiffusion attempt, explicit seed/chain/hotspots, PDB + `.trb`, terminal manifest. |
| `design_sequence` | ProteinMPNN design chains plus chain-local fixed positions, followed by independent sequence and residue-map checks. |
| `predict_structure` | Frozen no-MSA/no-template monomer prediction with raw scores. |
| `predict_complex` | Blind, OS-network-isolated complex prediction with raw PAE, interface PAE and ipTM. |
| `rosetta_score` | Rosetta physical-energy evidence. |
| `rosetta_relax` | Seeded FastRelax with an explicit output structure. |
| `rosetta_interface_score` | dG, dSASA, packstat and correctly named unsatisfied-H-bond delta. |
| `score_stability` | ESM-2 masked pseudo-log-likelihood, labelled sequence naturalness rather than thermodynamic stability. |
| `energy_minimize` | OpenMM refinement evidence, never proof that a design folds, binds or functions. |

There is intentionally no `design_binder`, hotspot-suggestion, approximate
interface analyzer or status pseudo-tool.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Narrow package export for the service. |
| [`__main__.py`](__main__.py) | Module entry point for stdio execution. |
| [`schemas.py`](schemas.py) | Closed MCP schemas and evidence descriptions for the nine tools. |
| [`server.py`](server.py) | Minimal MCP JSON-RPC framing and structured result projection. |
| [`service.py`](service.py) | Path confinement, command construction, digests, terminal records and post-run validation. |
| [`scientific_backend.py`](scientific_backend.py) | Separately executed optional-dependency worker for PyRosetta, ESM-2 and OpenMM. |
| [`README.md`](README.md) | English setup, boundary and file inventory. |
| [`README_zh.md`](README_zh.md) | Chinese setup, boundary and file inventory. |

## Upstream influence

The tool selection and some backend-wrapping patterns were informed by
`jasonkim8652/protein-design-mcp` revision
`7a45f13d5c7667513f4b3cfc47e472f3209b1be1` (Apache-2.0). This implementation
was rewritten around OpenAI4S's stdlib, provenance and reproducibility
constraints. It
does not vendor that project, its dependencies, containers or weights. Model
packages and weights retain their own upstream licenses.
