# Retrosynthesis Problem System: Core Prediction, Route Assessment, Execution Extensions, and Trust

## Scenario overview

“Plan a synthesis route for this target” is neither one model task nor a collection of fully independent scientific questions. Single-step proposals shape tree search, stock definitions change solved status, validators affect ranking, and experimental or process constraints determine whether a route is worth executing.

This document therefore uses four layers instead of a serial pipeline:

1. **Core retrosynthesis:** single-step proposal, multi-step search, and stock/user constraints;
2. **Route assessment:** reaction/route feasibility and route quality/diversity decisions;
3. **Synthesis execution extensions:** conditions/selectivity/yield and cost/supply/safety/green/scale-up decisions;
4. **Trust infrastructure:** evidence, calibration, uncertainty, provenance, and failure diagnosis.

Their scientific status differs. Q1, Q2, Q4, and Q6 are prediction or planning science; Q3 is the planning environment; Q5 and Q7 are multi-objective decisions; Q8 is assurance engineering. They can be benchmarked separately without being ontologically independent chemistry problems.

## Scope

- **Narrow computational retrosynthesis loop:** Q1–Q5, covering one-step proposals, tree search, fixed-stock termination, validation, and selection.
- **Executable synthesis planning:** Q1–Q7, adding conditions, selectivity, yield, cost, supply, safety, sustainability, and scale-up.
- **Auditable research system:** Q1–Q8, adding provenance, calibration, uncertainty, and honest failure.
- **Not exhaustive of all synthesis research:** multi-target/common-intermediate planning, laboratory closed loops, production scheduling, freedom-to-operate analysis, and organization-specific decisions remain extensions.

## Problem map

```text
┌──────────────────────── Core retrosynthesis ────────────────────────┐
│ Q1 Single-step proposal ─▶ Q2 Multi-step search ─▶ Q3 Stock/limits │
│ (center + precursors)       (AND-OR tree)          (environment)    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ candidate routes
                               ▼
┌──────────────────────── Route assessment ───────────────────────────┐
│ Q4 Reaction/route feasibility ─▶ Q5 Quality, ranking, diversity     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ reviewable routes
                               ▼
┌──────────────────── Synthesis execution extensions ────────────────┐
│ Q6 Conditions/selectivity/yield ─▶ Q7 Cost/supply/EHS/green/scale  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌──────────────────────── Trust infrastructure ───────────────────────┐
│ Q8 Evidence, calibration, uncertainty, provenance, failure         │
│ Q8 constrains Q1–Q7; it is not an end-of-report disclaimer.        │
└─────────────────────────────────────────────────────────────────────┘
```

## Classification

| ID | Problem | Nature | Separately benchmarkable | Role |
| --- | --- | --- | --- | --- |
| Q1 | Single-step proposal | Core prediction science | Yes | Produce one-reaction precursor candidates |
| Q2 | Multi-step search | Core planning science | Yes | Recursively compose proposals |
| Q3 | Stock and user constraints | Planning environment | Yes | Define termination and admissibility |
| Q4 | Reaction/route feasibility | Scientific validation | Yes, with several evidence layers | Identify invalid or weak routes |
| Q5 | Quality, ranking, diversity | Multi-objective decision science | Yes | Select complementary routes |
| Q6 | Conditions, selectivity, yield | Adjacent prediction science | Yes | Turn reaction graphs into testable hypotheses |
| Q7 | Cost, supply, safety, green, scale-up | Industrial decision problem | Yes | Decide whether execution is worthwhile |
| Q8 | Evidence, calibration, provenance, failure | Trust engineering | Yes | Keep prediction, evidence, and hypotheses distinct |

## Layer 1: core retrosynthesis

## Q1. Single-step retrosynthetic proposal

### Definition

Given a target product, generate precursor sets that could reach it in one reaction. Reaction-center prediction and precursor generation are two modeling decompositions of this task, not universally independent problems: graph/synthon methods expose a center, template methods instantiate precursors, and sequence/generative models may directly emit reactants.

### Fixed input and output

- **Input:** Canonical target, optional reaction class and structural constraints, Top-K.
- **Output:** Unordered precursor set, reaction SMILES, optional center, raw score/type, model/checkpoint provenance.

### Executable solution

Validate/canonicalize with RDKit; call an AiZynthFinder expansion policy or isolated `SyntheseusBackend`; canonicalize and deduplicate precursor sets; derive bond changes only when mapping exists; optionally add independent forward round-trip evidence; preserve each backend's score semantics.

### Metrics

Top-K precursor exact match or multi-reference feasibility recall, center bond precision/recall for explicit-center models, round-trip accuracy, invalid/empty/duplicate rates, diversity, latency, and cost.

### Current status

External isolation, RetroChimera checkpoint verification, response schemas, Top-K outputs, and structured errors exist. Independent forward validation, atom mapping, and live scientific-accuracy gates remain gaps.

## Q2. Multi-step route search

### Definition

Recursively compose Q1 proposals into complete routes from target to terminal materials.

### Fixed input and output

- **Input:** Target, expansion/filter policies, algorithm, budget, and Q3 termination definition.
- **Output:** AND-OR route trees, solved state, depth/leaves, search statistics, original rank, and provenance.

### Executable solution

Use configured AiZynthFinder search; preserve OR molecule choices and AND precursor requirements; bound cycles, repeated states, depth, expansions, concurrency, and time; save raw exports/checkpoints; keep `solved=False` trees diagnostic rather than presenting them as complete routes.

### Metrics

Solved rate, Top-N reference-route recovery, tree/reaction/intermediate similarity, expansions/depth/time/memory/model calls, timeout rate, replay consistency, and cost per solved target.

### Current status

Safe command construction and route-tree normalization exist. Live search requires the optional AiZynthFinder environment/assets; offline fixtures do not establish planning accuracy.

## Q3. Stock, termination, and user constraints

### Definition

Q3 is not a chemistry predictor. It defines the planning environment: which leaves count as available and which routes violate price, lead-time, structural, equipment, or user constraints.

### Fixed input and output

- **Input:** Frozen stock, salt/tautomer/stereo policy, price/lead-time limits, and hard constraints.
- **Output:** Per-leaf stock match/tier/version/unresolved reason and per-route constraint status.

### Executable solution

Freeze/hash stock; perform exact canonical matching before declared normalization tiers; separate stock membership from timestamped supplier evidence; enforce hard constraints explicitly; expose match rules and unresolved leaves rather than only a solved Boolean.

### Metrics

Stock precision/recall, full-termination rate, unresolved leaves, per-tier matches, constraint violations, and provenance completeness.

### Current status

AiZynthFinder stock termination and starting-material reporting exist. Stable per-leaf match provenance and commercial constraints remain incomplete.

## Layer 2: route assessment

## Q4. Reaction-step and route feasibility

### Definition

Determine whether route structure is coherent, whether steps have plausible support, and whether one weak step invalidates the route. Q4 is layered rather than one universal feasibility score: deterministic integrity, mapping/conservation, independent forward prediction, literature/ELN precedent, and expert/experimental evidence are distinct.

### Fixed input and output

- **Input:** Fixed route tree, reactions/templates, optional forward model and evidence corpus.
- **Output:** Layered errors/warnings, forward evidence, precedent status, weakest step, and unknowns.

### Executable solution

Run deterministic checks first; add mapped bond-change/conservation checks; perform round trips with a model independent of Q1; distinguish exact from similarity precedents; aggregate route risk with weakest-link awareness; never collapse heterogeneous evidence into an uncalibrated probability.

### Metrics

Structural errors/warnings, mapping/conservation pass rate, forward rank/round-trip, precedent coverage, expert-label precision/recall/AUROC, weakest-step detection, and appropriate abstention.

### Current status

`structural_audit.py` checks trees, SMILES, precursors, reaction identity, and simple elemental deficits. It is not a forward model. Forward/mapping/evidence evaluators and route-level weakest-link aggregation remain gaps.

## Q5. Route quality, ranking, deduplication, and diversity

### Definition

Select routes worth reviewing under conflicting objectives while preserving genuinely different chemical strategies.

### Fixed input and output

- **Input:** Fixed candidates, Q3 constraints, Q4 findings, optional costs/preferences.
- **Output:** Multi-objective/Pareto ranking, signatures, duplicate provenance, diversity diagnostics, and Top-N.

### Executable solution

Apply hard constraints before scores; evaluate solved state, steps, stock completion, weakest step, evidence, cost, and complexity; merge stable route signatures while retaining counts/sources; select diversity across reaction/product/precursor/leaf features; keep weights evaluator-independent; expose `diversity_relaxed` when similar routes are restored.

### Metrics

Top-N valid/solved/stock-complete fraction, chemist NDCG/Spearman/pairwise agreement, reference recovery, duplicate removal, route clusters/feature coverage, maximum similarity, Pareto coverage, and weight-perturbation stability.

### Current status

`workflow.py` and `route_review.py` implement baseline ranking, signature deduplication, Jaccard diversity, and `diversity_relaxed`. They are not an industrially calibrated route-value model and do not yet contain complete Q6/Q7 data.

## Layer 3: synthesis execution extensions

## Q6. Conditions, selectivity, and yield

### Definition

For one fixed reaction, propose catalysts/reagents/solvents/temperature/time and estimate selectivity/yield uncertainty. This is essential to full CASP but adjacent to narrow retrosynthesis generation. A benchmark fixes the reaction so upstream planning cannot inflate its score.

### Fixed input and output

- **Input:** Fixed reactants/product/optional center, literature/ELN, optional condition model.
- **Output:** Top-K condition sets, selectivity/yield predictions, scope, sources, and unknowns.

### Executable solution

Retrieve exact/close precedents; optionally call a dedicated predictor; preserve several candidates and dependencies among context variables; use an LLM only to organize evidence/conflicts/experiments; return unknown rather than fabricated yield.

### Metrics

Top-K reagent/catalyst/solvent recall, condition-set similarity, temperature/time error, chemo-/regio-/stereo-selectivity accuracy, yield MAE/calibration, evidence-backed rate, and abstention.

### Current status

Guarded evidence retrieval and labelled LLM hypotheses exist. A validated condition/yield backend does not, so Q6 is assisted rather than scientifically solved.

## Q7. Cost, supply, safety, sustainability, and scale-up

### Definition

Decide whether a chemically plausible route is worth executing under a particular organization, region, date, facility, schedule, and scale. This is industrial multi-objective decision-making, not one chemistry prediction.

### Fixed input and output

- **Input:** Route/conditions, dated regional supply data, facility limits, EHS rules, scale, and green targets.
- **Output:** Material cost, lead-time/supply risks, hazards, PMI/E-factor where calculable, facility/scale conflicts, and a Pareto diagnosis.

### Executable solution

Use dated supply data; audit hazardous materials, thermal/gas/pressure/temperature and unstable-intermediate risks; compute mass-based green metrics only with mass balance; check equipment/purification/solvent swaps/stability at target scale; report a Pareto front instead of a fake industrial-feasibility scalar.

### Metrics

Cost/lead-time error, supply-risk and hazard recall, severity agreement, PMI/E-factor error, facility/scale violations, and process-chemist pairwise preference.

### Current status

Reports can carry hypotheses, but no deterministic supply/EHS/facility/mass-balance connectors exist. Q7 is an explicit gap and cannot be replaced by route length.

## Layer 4: trust infrastructure

## Q8. Evidence, calibration, uncertainty, provenance, and failure

### Definition

Make clear which claims are backend predictions, deterministic calculations, external evidence, expert observations, or LLM hypotheses; return reliable unknown/failure states. Q8 is assurance engineering, not chemical prediction.

### Fixed input and output

- **Input:** Q1–Q7 outputs, manifests, raw exports, sources, environments, and logs.
- **Output:** Source-labelled JSON, calibration/uncertainty, provenance, structured failures, and review artifacts.

### Executable solution

Label source types; record model/version/checkpoint/training/runtime/error identity; record stock/config/budget/raw exports/lineage; preserve retrieval request/time/digest; report calibration only where valid and mark other scores uncalibrated; return failure/unknown for missing assets, timeouts, unsolved searches, absent evidence, and conflicts.

### Metrics

Provenance completeness, replay/digest consistency, source-label accuracy, calibration error or correct uncalibrated labels, failure classification, appropriate abstention, and unsupported claims.

### Current status

Path-free manifests, checkpoint hashes, versioned responses, structured errors, artifact provenance, dashboards, and reports exist. A task-level confidence/calibration/evaluator schema remains incomplete.

## Independent benchmark contracts

| Benchmark | Fixed input | Evaluated | Isolated influence |
| --- | --- | --- | --- |
| Single-step | target + Top-K | Q1 | No tree search or evaluator-driven tuning |
| Multi-step | target + policy + stock + budget | Q2 | Freeze Q1 and Q3 |
| Stock/constraints | fixed leaves/routes + stock | Q3 | Planner quality cannot affect matching |
| Feasibility | fixed reactions/routes | Q4 | Generator cannot validate itself |
| Ranking | fixed route set | Q5 | Candidate recall cannot affect ranking |
| Conditions/yield | fixed reaction | Q6 | Planner quality cannot affect conditions |
| Industrial decision | fixed route/conditions/dated data | Q7 | Freeze region/date/scale/facility |
| Trust output | recorded outputs/failures | Q8 | Replay without live models |

## Automation status

```text
Q1 single-step proposal                ✓ backend integrated; live accuracy needed
Q2 multi-step search                   ✓ optional AiZynthFinder environment/assets
Q3 stock/constraints                   △ termination works; leaf provenance incomplete
Q4 deterministic audit                ✓
Q4 forward/literature/expert evidence  △ independent evaluators needed
Q5 ranking/deduplication/diversity     ✓ baseline implemented
Q6 conditions/selectivity/yield        △ evidence + LLM hypotheses today
Q7 industrial supply/EHS/green/scale   ✗ connectors and deterministic metrics needed
Q8 provenance/failure/review           ✓ baseline; calibration schema incomplete
Experimental success and real scale-up ✗ expert and laboratory confirmation
```

## Unified hard constraints

1. Ground Truth is evaluator-only for the relevant benchmark.
2. Independent benchmarks freeze their inputs.
3. Canonicalization, stock, salts/tautomers/stereo rules are predeclared.
4. Search comparisons use equal or normalized budgets.
5. Raw backend scores remain model-specific and are not success probabilities.
6. Route nodes come only from declared backends; LLMs cannot repair and relabel them.
7. Q4 validators should be independent of Q1 generators where possible.
8. Deterministic audit precedes and survives language interpretation.
9. Only frozen-stock matches are in-stock; live supply is dated regional evidence.
10. Prediction, calculation, evidence, expert observation, and hypothesis have distinct labels.
11. Unknown yield, cost, lead time, PMI, or facility data is never fabricated.
12. Deduplication retains signatures/counts/sources; restored similar routes expose `diversity_relaxed`.
13. External models retain version/checkpoint/training/runtime/failure provenance.
14. Unsolved subproblems return structured failure/unknown rather than fluent success prose.

## Reference boundaries

- AiZynthFinder 4.0 separates one-step models, search, stock, and route scoring: https://pmc.ncbi.nlm.nih.gov/articles/PMC11112899/
- Retro* formulates multi-step planning as neural-guided AND-OR tree search: https://arxiv.org/abs/2006.15820
- PaRoutes separately evaluates solved targets, reference-route quality, and diversity: https://doi.org/10.1039/D2DD00015F
- Early seq2seq work defines precursor prediction as a module in multi-step retrosynthesis: https://doi.org/10.1021/acscentsci.7b00303
- Condition recommendation treats catalyst, solvent, reagent, and temperature as a separate prediction task: https://doi.org/10.1021/acscentsci.8b00357
