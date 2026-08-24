---
name: protein-design-mcp
description: >-
  Compose auditable protein-design operations through the configured OpenAI4S
  protein-design MCP connector: target-conditioned RFdiffusion backbone
  generation, constrained ProteinMPNN sequence design, monomer or complex
  structure prediction, Rosetta scoring and relaxation, ESM-2 sequence
  naturalness scoring, and OpenMM minimization. Use when designing or
  redesigning proteins, creating target-binding proteins, preserving sequence
  motifs, validating candidate structures or complexes, refining structures,
  or ranking protein-design candidates with reproducible model evidence.
---

# Compose protein-design operations over MCP

Select atomic tools according to the scientific objective. Do not force every
task through one pipeline, and do not treat a model score as experimental proof.

## Discover the connector

Find the enabled connector with `host.mcp.list()`, then inspect it with
`host.mcp.tools(server)`. The connector can expose:

- `generate_backbone`
- `design_sequence`
- `predict_structure`
- `predict_complex`
- `rosetta_score`
- `rosetta_relax`
- `rosetta_interface_score`
- `score_stability`
- `energy_minimize`

Discovery shows that the server started. Before running a model, also verify
its execution route, external environment, pinned revision, checkpoint,
compute resources and required network-isolation mechanism.

## Select compute before provisioning

Call `host.accelerator_status()` before any GPU-only operation. It probes the
daemon's local GPUs first and then lists configured SSH GPU routes. These are
different from a BYOC provider catalogue and from model-backend readiness.

When both a local route and one or more SSH routes are candidates, ask the user
to choose `local` or `ssh:<alias>` before downloading, installing or launching
anything. Do not silently prefer either route. When only one route exists,
state the selected `execution_target` and record it in the bring-up evidence.
An empty SSH registry is not evidence that the local machine has no GPU, and a
missing Docker executable does not make a natively usable local GPU disappear.

## Acquire checkpoints only when needed

Do not ask for, locate or download checkpoints during generic connector
discovery. Only enter this flow after selecting an operation whose tool schema
requires `checkpoint_path`. Before that call, inspect whether the user already
supplied a path. If not, stop provisioning and ask whether they have an existing
local checkpoint; request its path plus any known digest. Do not search for or
download weights while that question is unanswered, and do not make them
download a second copy merely because it is outside a conventional directory.

If the user says there is no local checkpoint, use the normal approved network
and tool bring-up controls to download it. The framework does not maintain a
closed list of allowed scientific sources: resolve the source selected for this
run to an immutable version, prefer an upstream-published checksum, compute the
downloaded file's SHA-256 independently, and retain source URL, size and digest.
An observed digest with no independently trusted reference proves transfer
identity, not that the file is the intended model; report that distinction.

After acquiring code, environment or weights, run a small real inference
canary on the selected execution target. The canary must exercise the same
adapter, backend revision and checkpoint that the formal call will use, produce
the output types the adapter promises, and pass the adapter's parser and digest
checks. Set `run_mode="canary"` on this attempt. Record failed bring-up attempts.
Only after its result contains a verified `bringup_admission` may a new attempt
use `run_mode="formal"`. A formal attempt made too early ends in a durable
failure, so retry it with a new `attempt_id` after admission rather than reusing
the failed ID. Only after a canary reaches a verified
terminal success may the backend be used in the user's formal work. Then retry
the original scientific operation instead of ending the task with “backend not
configured.” If permission, licensing, source integrity, disk capacity or the
canary genuinely blocks bring-up, report that specific blocker and do not
fabricate results.

## Choose only the operations the task needs

Typical compositions include:

- target-conditioned binder design: `generate_backbone` → `design_sequence` →
  `predict_structure` and `predict_complex` → interface scoring;
- backbone sequence redesign: `design_sequence` → `predict_structure` →
  optional physical scoring;
- fixed-motif sequence design: `design_sequence` with explicit per-chain fixed
  positions → structure validation;
- structure refinement: `rosetta_relax` or `energy_minimize` → score the input
  and refined structures with the same method;
- candidate ranking: combine sequence, monomer, complex and physical evidence
  while retaining the individual scores and provenance.

These are examples, not mandatory pipelines. Start from the user's design
objective and constraints, then choose the smallest informative set of calls.

## Record reproducible attempts

Give each model execution a stable `attempt_id` and explicit seed. Pin the
backend revision and checkpoint SHA-256, use a dedicated output directory, and
retain the resolved configuration, command, residue maps, raw outputs and
terminal record. Reusing the same attempt and configuration is idempotent;
changing the configuration under an existing attempt ID is a conflict.

One call to `generate_backbone` produces one design. Run multiple attempts with
distinct IDs and seeds when sampling a population. A failed attempt remains
part of the provenance rather than being silently discarded.

## Generate target-conditioned binder backbones

The current `generate_backbone` contract requires a target PDB, explicit target
chain or chains, validated target hotspot residues and a binder length. It
verifies the local RFdiffusion checkpoint and returns both PDB and `.trb`
mapping outputs.

Do not describe this operation as epitope-free or purely function-guided de
novo design: the hotspot list supplies structural contact-region information.
If the task does not provide a contact region, epitope selection is a separate
scientific step and its assumptions must be reported.

The current schema also does not express unconditional monomer generation,
motif-scaffolding contigs, symmetric oligomer generation or membrane-specific
constraints. Use another suitable atomic connector or extend this schema before
claiming those backbone-generation capabilities.

## Design sequences without losing constraints

Call `design_sequence` with every input chain represented in
`fixed_positions`. Values are `"all"` or chain-local, 1-based sequence
positions. Include only mutable chains in `design_chains`; mark fixed target or
context chains as `"all"`.

The connector uses ProteinMPNN's `--pdb_path_chains` and
`--fixed_positions_jsonl` inputs and independently rejects output when a fixed
chain or motif changes, a chain length changes, or the residue map does not
close. Inspect this validation before using a sequence downstream.

## Predict structures and complexes without self-conditioning

Use `predict_structure` for monomer evidence and `predict_complex` for blind
sequence-only complex evidence. Formal prediction calls require:

- `msa_mode="single_sequence"`;
- a local checkpoint bundle with verified digests;
- fixed model type, recycles, model count and seed;
- templates and initial guesses disabled;
- an operator-configured OS-level network-isolation prefix.

Preserve raw confidence values and PAE. Treat pLDDT, pTM, ipTM and interface PAE
as model confidence, not as proof of folding, binding, affinity or function.
Do not feed a generated complex back as a template or initial guess for its own
validation.

## Add physical and sequence evidence carefully

Use `rosetta_interface_score` for `dG_separated`, `dSASA`, `packstat`, interface
residue count and `interface_delta_unsat_hbonds`. The final field is a change in
unsatisfied hydrogen bonds, not a count of formed interface hydrogen bonds.

Treat `score_stability` as ESM-2 masked pseudo-log-likelihood or sequence
naturalness, not thermodynamic stability. Treat `energy_minimize` as local
force-field refinement, not evidence that a candidate folds or binds.
`rosetta_relax` is optional; when using it, compare consistently scored input
and relaxed structures and retain both.

## Rank and report without collapsing evidence

Apply hard task constraints before ranking. Keep evidence types separate,
report failed attempts, and preserve structural diversity instead of selecting
only near-duplicates with the best value from one model.

When these tools are used inside a benchmark, keep any withheld references or
labels inaccessible during candidate generation and ranking. This is an
optional benchmark-integrity rule, not a restriction on ordinary protein
design use and not a responsibility assigned to this connector.
