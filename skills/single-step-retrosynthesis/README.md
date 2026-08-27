# Single-step Retrosynthesis Skill

An executable recipe for one-product-to-one-step-precursors inference. It uses
the existing isolated RetroChimera/Syntheseus adapter and keeps the boundary
between a precursor proposal and a complete multi-step route explicit.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Model choice, isolated invocation, candidate normalization, output contract, scientific limits, and failure handling. |
| [`README.md`](README.md) | English directory index. |
| [`README_zh.md`](README_zh.md) | Chinese directory index. |

The executable class-unknown benchmark protocol is shared from
[`../retrosynthesis_planning/single_step_benchmark.py`](../retrosynthesis_planning/single_step_benchmark.py)
so normalization and evaluator semantics have one implementation rather than a
copy in each Skill.
