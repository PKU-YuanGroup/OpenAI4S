---
name: rfdiffusion
description: >
  Generate de novo protein backbones with RFdiffusion for protein-target
  binders, hotspot-conditioned interfaces, motif scaffolding, partial
  diffusion, or symmetric assemblies. Use this skill when a design workflow
  needs reproducible RFdiffusion contigs, residue mappings, checkpoints,
  seeds, batch execution, and handoff to ProteinMPNN plus independent
  structure validation. RFdiffusion generates backbones; it does not validate
  folding or binding.
license: MIT
origin: openai4s
category: biomodels
requirements: [gpu]
metadata:
  display-name: RFdiffusion
  third_party:
    - kind: recipe
      name: Adaptyv protein-design-skills RFdiffusion recipe
      license: MIT
      terms_url: https://github.com/adaptyvbio/protein-design-skills/blob/b231232025e3db41e9c637abc1e8082e915f6cb1/LICENSE
    - kind: code-and-weights
      name: RFdiffusion
      license: BSD-3-Clause
      terms_url: https://github.com/RosettaCommons/RFdiffusion/blob/main/LICENSE
---

# RFdiffusion

Use RFdiffusion for the backbone-generation stage of a protein-design
workflow. Treat its output as a structural proposal, not as evidence that a
sequence folds or binds. Design sequences afterward with `proteinmpnn` (or
`ligandmpnn` when non-protein atoms must be visible), then validate both the
isolated design and the target–design complex with an independent predictor
such as `boltz`, `chai1`, or `alphafold2`.

The adapted recipe is MIT-licensed. The upstream RFdiffusion code and the
model weights referenced by its README are BSD-3-Clause; record the exact
upstream commit and checkpoint digest used by each run.

## Preflight: freeze the design contract

Before inference:

1. Preserve the original chain IDs and residue numbers. Write an explicit map
   if the input is renumbered, cropped, or has insertion codes.
2. Audit missing residues/atoms, alternate locations, non-standard residues,
   and biological assembly choice. Do not silently remove cofactors or chains.
3. For binder design, confirm proposed hotspot residues are surface-accessible
   and belong to the intended target chain. A typical pilot uses 3–6 spatially
   coherent hotspots, but the biological interface determines the final set.
4. Freeze the target PDB digest, RFdiffusion commit, checkpoint digest,
   contig string, hotspot list, seed policy, number of designs, and output
   prefix in a machine-readable manifest.
5. Keep target-only coordinates separate from any withheld reference binder.
   Do not leak a reference complex into generation or validation.

Do not trim a target merely to make inference cheaper unless the retained
construct is biologically justified and the residue map is preserved.

## Install and invoke the official runner

RFdiffusion is a source repository rather than an OpenAI4S sidecar. Prepare a
pinned GPU environment, clone a fixed revision of the official repository,
install it there, and download the documented checkpoint. Verify every
download before starting a campaign. Follow the upstream CUDA/PyTorch/DGL
compatibility instructions for the selected revision; do not improvise a
version matrix from this recipe.

Do not install RFdiffusion into OpenAI4S's shared `struct` environment. That
environment intentionally targets portable Python 3.13 with a CPU PyTorch
build, whereas upstream RFdiffusion publishes a Python 3.9, CUDA-specific
PyTorch/DGL stack. Use a pinned dedicated conda environment or the official
RFdiffusion Docker image pinned by digest. `openai4s setup --profile full`
creates the shared `struct` environment; it does not provision RFdiffusion,
GPU drivers, model weights, or a container image.

Run scripts from the RFdiffusion repository root. The official inference entry
point is `scripts/run_inference.py`:

```bash
./scripts/run_inference.py \
  inference.input_pdb=target.pdb \
  'contigmap.contigs=[A1-150/0 70-100]' \
  'ppi.hotspot_res=[A45,A67,A89]' \
  inference.output_prefix=results/backbones/design \
  inference.num_designs=48
```

This example fixes target chain A residues 1–150, inserts a chain break, and
generates a 70–100-residue binder chain. The output is backbone-only: designed
residues are represented as glycine by default. That is expected and is the
handoff point to inverse folding.

Quote the **entire Hydra list override**, including brackets. Shell tokenization
otherwise splits a contig containing spaces before Hydra sees it. Inside the
contig:

- `/0 ` means a chain break: slash, zero, then a space.
- `/` without the zero joins segments in the same output chain.
- `A10-30` selects fixed input coordinates; `20-40` requests a generated
  segment of variable length.
- The order of contig segments controls the output topology. Never infer
  output residue identity from the PDB alone; read the mapping in the `.trb`.
- Hotspots use input chain plus residue number, with no spaces between list
  items. Quote that whole list override too.

Common malformed variants are an unquoted contig with spaces, a comma between
contig segments, a missing `/0` at a desired chain break, or hotspot numbers
without chain IDs.

## Motif scaffolding

For motif scaffolding, retain motif coordinates as an input-coordinate segment
and generate flanking residues in the same chain. For example, if the input
contains target chain A and a functional motif at B10–B24:

```bash
./scripts/run_inference.py \
  inference.input_pdb=target_and_motif.pdb \
  'contigmap.contigs=[A1-150/0 20-40/B10-24/20-40]' \
  'ppi.hotspot_res=[A45,A67,A89]' \
  inference.output_prefix=results/motif_scaffolds/design \
  inference.num_designs=48
```

Confirm the exact syntax against the pinned upstream revision before spending
a large GPU budget. After generation, compute motif backbone RMSD and the
motif–target contact geometry using the `.trb` input/output residue mapping.
Do not assume PDB residue numbers survived contig assembly. Preserve motif
sequence positions during downstream sequence design.

## Batch safely and make runs resumable

One large foreground call can exceed the OpenAI4S cell watchdog. Split a
campaign into deterministic batches or use `host.exec_background` / remote
compute. Assign each batch a disjoint output prefix and seed range. Keep an
append-only manifest with, at minimum:

```json
{
  "batch_id": "round1_batch03",
  "status": "completed",
  "input_pdb_sha256": "...",
  "rfdiffusion_commit": "...",
  "checkpoint_sha256": "...",
  "contigs": "[A1-150/0 70-100]",
  "hotspots": ["A45", "A67", "A89"],
  "seed_start": 2000,
  "requested": 8,
  "completed": 8,
  "output_prefix": "results/backbones/r1_b03/design"
}
```

Poll the job rather than repeatedly submitting it. On resume, verify completed
PDB/TRB pairs and their digests before scheduling missing design indices. Do
not overwrite an earlier round when contigs, hotspots, checkpoints, or target
coordinates change; create a new round and record why it changed.

## Preserve and interpret every upstream output

For each design, retain:

- the final PDB backbone;
- the `.trb`, which stores the sampled contig/config plus residue mappings and
  masks needed to audit the design;
- the denoising trajectory files when produced, or an explicit retention rule
  if storage policy excludes them;
- stdout/stderr, the resolved Hydra config, the batch manifest, and digests.

Reject or flag outputs that are incomplete, violate requested length/chain
layout, lose fixed target or motif coordinates, clash severely, or cannot be
mapped back to input residues. Secondary structure visible in a generated PDB
is not a sufficient QC result.

## Downstream scientific loop

1. Use the `.trb` mapping to separate fixed target/motif positions from
   designable binder positions.
2. Generate multiple sequences per accepted backbone with `proteinmpnn`;
   preserve fixed motif residues and record temperature, seeds, and model
   checkpoint.
3. Fold each binder alone and compare it with the intended binder backbone.
4. Re-predict the target–binder complex independently. For benchmark use, do
   not provide the designed complex as a template or initial guess; this is a
   stricter anti-self-validation rule than some production pipelines.
5. Compute interface metrics on the independently predicted complex, including
   hotspot/epitope contacts, non-epitope contacts, buried surface area, clashes,
   and confidence fields appropriate to the predictor.
6. Apply hard constraints first, then rank and diversity-cluster survivors.
   Use failure distributions to justify any next RFdiffusion round.

RFdiffusion scores, a generated interface, ProteinMPNN likelihood, monomer
confidence, or any one complex-confidence field alone is not proof of binding.
Report computational candidates as hypotheses requiring experimental testing.
