# Protein-design MCP Skill

[中文说明](README_zh.md)

This Skill teaches the agent to compose the bundled protein-design MCP tools
for general protein design and redesign work. It covers target-conditioned
binder backbones, constrained sequence design, monomer and complex prediction,
physical scoring and relaxation, sequence-naturalness scoring, minimization,
and reproducible candidate comparison.

It also states the current scientific boundary explicitly: the RFdiffusion
tool requires target hotspots and does not yet express epitope-free,
motif-scaffolding, unconditional or membrane-aware backbone generation.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | General tool-selection workflows, reproducibility controls, current capability gaps and model-evidence boundaries. |
| [`README.md`](README.md) | English directory boundary and inventory. |
| [`README_zh.md`](README_zh.md) | Chinese directory boundary and inventory. |

The model packages, weights and GPU environments are not vendored by this
Skill. Configure the connector and its external backends separately.
