# RFdiffusion Skill

RFdiffusion generates protein backbones for de novo binder design, hotspot
conditioning, and motif scaffolding. This directory provides an operational
recipe for the external GPU software; it does not vendor RFdiffusion code or
weights and does not claim that generated backbones fold or bind.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Reproducible RFdiffusion setup and inference guidance: correct Hydra quoting and contig semantics, residue and `.trb` provenance, batched execution, motif scaffolding, and the required handoff to ProteinMPNN plus independent monomer/complex validation. |
