# Retrosynthesis scientific scenarios

[中文](README_zh.md)

This directory contains six Chinese benchmark specifications. Each file defines
one independently runnable and independently scored scientific problem; the
files are not stages of a mandatory pipeline.

## Files

| File | Scope |
| --- | --- |
| [`01_single_step_retrosynthesis.md`](01_single_step_retrosynthesis.md) | Product-to-precursor prediction under a frozen USPTO-50K test set. |
| [`02_multistep_route_planning.md`](02_multistep_route_planning.md) | Budgeted target-to-stock route search on frozen PaRoutes tasks. |
| [`03_atom_mapping.md`](03_atom_mapping.md) | Atom correspondence and bond-change recovery for complete reactions. |
| [`04_forward_prediction.md`](04_forward_prediction.md) | Reactant/reagent-to-product prediction on a frozen patent test split. |
| [`05_condition_recommendation.md`](05_condition_recommendation.md) | Top-K categorical reaction-condition recommendation. |
| [`06_yield_estimation.md`](06_yield_estimation.md) | Yield regression and uncertainty on Buchwald-Hartwig OOD splits. |
| [`README_zh.md`](README_zh.md) | Chinese directory guide and shared scientific boundary. |

## Query, GT, and generated codebases

| Directory | Scope |
| --- | --- |
| [`queries/`](queries/) | Six frozen OpenAI4S prompts with explicit public input-file contracts. |
| [`gt_codebases/`](gt_codebases/) | Six repository-reviewed GT reference entrypoints. |
| [`openai4s_codebases/`](openai4s_codebases/) | Same-named outputs from actual OpenAI4S CLI runs and generation provenance. |
| [`pipelines/`](pipelines/) | Deprecated compatibility aliases for the GT entrypoints. |
| [`test_cases/`](test_cases/) | Evaluation fixtures, database registry, one-command installer, and private evaluator entrypoint. |

The pipeline process reads only installed `public/` files. The evaluator reads
the frozen output and `private_evaluator/` only after pipeline completion.
GT code must not be presented as OpenAI4S-generated code. An absent API key or
failed generation remains explicit in the generated-code manifest rather than
being replaced by copied reference code.

The parent [`../SCENARIO.md`](../SCENARIO.md) and
[`../SCENARIO_zh.md`](../SCENARIO_zh.md) remain the overview and dependency
map. These detailed files are the canonical benchmark-design drafts. A scenario
must not be called release-ready until every source snapshot, split, license,
checksum, and private-evaluator boundary marked in that file has been frozen.
