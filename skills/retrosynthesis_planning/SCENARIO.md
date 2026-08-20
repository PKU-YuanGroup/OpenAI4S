# Model-backed scientific problems in retrosynthesis planning

[中文说明](SCENARIO_zh.md)

## Scenario overview

This Scenario covers locally deployable, open-model assistance for planning and
reviewing synthesis routes from a target molecule. Retrosynthesis is not one
model and the tasks below are not mandatory stages in a fixed pipeline. Each
asks a different scientific question with its own input, output, model, and
evaluator:

1. single-step precursor generation;
2. multi-step route planning;
3. reaction atom mapping and centre identification;
4. forward product prediction and round-trip checking;
5. reaction-condition recommendation;
6. reaction-yield estimation.

Problems 1 and 2 are the necessary core of narrow retrosynthesis planning.
Problems 3–6 are separately benchmarkable reaction-understanding, validation,
and execution-support tasks. They can improve route review but are neither
required in every planner nor experimental proof.

This file is the overview and boundary map. Six complete Chinese benchmark
designs—with dataset construction, private ground truth, checkpoints, input
trees, metrics, failure cases, and release blockers—are indexed in
[`scenarios/README.md`](scenarios/README.md).

## Relationship, not pipeline

```text
P1 product -> precursor sets
          │ expansion policy
          ▼
P2 target + stock -> route trees
          │ fixed candidate reactions
          ├──────────────┬──────────────────┐
          ▼              ▼                  ▼
P3 reaction mapping  P4 forward product  P5 conditions
                                            │ full context
                                            ▼
                                        P6 yield
```

The arrows show input dependencies. P3 cannot map a target-only query, and P6
cannot score a route step whose reaction context is unspecified.

## Problem and implementation summary

| ID | Scientific problem | Minimum input | Output | Default model | Repository status |
| --- | --- | --- | --- | --- | --- |
| P1 | Single-step precursor generation | product SMILES | Top-K precursor sets | RetroChimera 1 | isolated worker, checkpoint verification, structured response |
| P2 | Multi-step route planning | target, policy, stock, budget | solved/unsolved route trees | AiZynthFinder | command builder, import, normalization, audit, ranking |
| P3 | Atom mapping/reaction centre | complete reaction SMILES | mapped reaction, changed bonds | RXNMapper | standalone Skill; optional model environment required |
| P4 | Forward product prediction | reactants and reagents | Top-K products | ReactionT5v2-forward | standalone Skill; optional model environment required |
| P5 | Condition recommendation | fixed reaction | condition sets, optional temperature | Parrot | conditional Skill; checkpoint terms require review |
| P6 | Yield estimation | reactants, reagents, product | predicted yield | ReactionT5v2-yield | standalone Skill; in-domain screening only |

## Problem 1. Single-step precursor generation

### Science query

Given one target product, generate precursor sets that could form it in one
reaction without accessing reference precursors or route ground truth.

### Goal, input, and output

- **Goal:** propose one-step disconnections for chemist review and planner
  expansion.
- **Input:** canonical target, Top-K, optional structural constraints, frozen
  model/checkpoint.
- **Output:** unordered precursor sets, reaction SMILES, raw score/type,
  optional centre, parse/duplicate state, and model provenance.

### Technique and implementation

Use RetroChimera 1 through the isolated Syntheseus worker. Canonicalize and
deduplicate unordered components while retaining source rank. Use
ReactionT5v2-retrosynthesis only as a diversity model; do not average scores
across model families. The repository Skill is `single-step-retrosynthesis`,
and `SyntheseusBackend` is already implemented.

### Independent metrics

Precursor-set Top-1/Top-K exact match, multi-reference recall, invalid/empty/
duplicate rates, reaction-centre bond F1 where applicable, diversity, latency,
throughput, and memory.

### Hard constraints

Ground truth must be evaluator-only; precursor components compare as unordered
sets; uncalibrated scores are not experimental probabilities; a missing centre
must not be invented; structural filtering cannot use reference answers.

## Problem 2. Multi-step route planning

### Science query

Given a target, frozen one-step policy, frozen stock, and bounded search budget,
find complete routes whose terminal materials all reach stock.

### Goal, input, and output

- **Goal:** compose local proposals into valid AND-OR route trees.
- **Input:** target, expansion/filter policies, stock snapshot, algorithm,
  budget, and hard user constraints.
- **Output:** solved/unsolved trees, reactions, leaves, stock matches, search
  statistics, raw rank/score, and configuration provenance.

### Technique and implementation

Use AiZynthFinder. Molecules are OR choices and all precursors of one reaction
are an AND requirement. Bound depth, cycles, repeated states, expansions, and
wall time; retain raw exports and checkpoints. OpenAI4S safely constructs the
CLI, normalizes route trees, retains unresolved leaves, audits structure, and
deduplicates review routes. Those review functions are engineering support, not
additional scientific predictors.

### Independent metrics

Solved-target rate, Top-N reference recovery, reaction/intermediate/tree
similarity, steps and unresolved leaves, expansions, model calls, time, memory,
timeouts, replay consistency, and cost per solved target.

### Hard constraints

Freeze policy/filter/stock/budget across planners; never present `solved=False`
as complete; preserve AND semantics; route ground truth cannot guide search;
live supplier results cannot silently mutate benchmark stock.

## Problem 3. Reaction atom mapping and centre identification

### Science query

For a complete known reaction, determine atom correspondence and which bonds
were formed, broken, or changed order.

### Goal, input, and output

- **Goal:** produce auditable correspondence and bond changes for reaction
  analysis, cleaning, templates, and route-step checks.
- **Input:** fixed complete reaction SMILES and participant/reagent rules.
- **Output:** mapped reaction, mapper confidence, changed bonds, unmapped atoms,
  conservation warnings, and provenance.

### Technique and implementation

Use RXNMapper, preferably `BatchedMapper`, then derive bond changes from RDKit
bond tables keyed by atom-map-number pairs. This is not target-only
retrosynthesis; low mapping confidence is not reaction infeasibility. The
repository Skill is `reaction-atom-mapping`.

### Independent metrics

Atom-mapping accuracy, changed-bond precision/recall/F1, conservation pass
rate, invalid mapping rate, confidence calibration, throughput, and failure
isolation.

### Hard constraints

Freeze both reaction sides; never silently move/delete participants to improve
mapping; retain original and mapped strings; derive changes from map numbers;
do not interpret mapping confidence as reaction success.

## Problem 4. Forward product prediction and round-trip checking

### Science query

Given candidate reactants and reagents, which products are predicted, and does
the intended target appear in Top-K?

### Goal, input, and output

- **Goal:** test model agreement with a retrosynthetic proposal and expose
  competing products.
- **Input:** separated reactant and reagent fields, optional intended product
  used only after prediction, beam and Top-K.
- **Output:** ranked canonical products, raw scores, parse state, intended rank,
  Top-K recovery, and provenance.

### Technique and implementation

Use `sagawa/ReactionT5v2-forward` with its declared input format. Canonicalize
all outputs and retain invalid raw strings. Round-trip recovery is agreement,
not feasibility; shared training data makes the evidence correlated. The Skill
is `reaction-forward-prediction`.

### Independent metrics

Product Top-1/Top-K accuracy, reciprocal rank, recovery rate, invalid/duplicate
rate, stereochemistry-sensitive accuracy, latency, throughput, and memory.

### Hard constraints

The model cannot read the intended product; freeze one forward checkpoint when
comparing backward models; record missing reagents; do not multiply unjointly
calibrated forward/backward scores; never emit `feasible=True` from recovery.

## Problem 5. Reaction-condition recommendation

### Science query

For a fixed reaction, which catalyst, reagent, solvent, and checkpoint-supported
temperature hypotheses should be validated first?

### Goal, input, and output

- **Goal:** narrow literature/ELN retrieval and experimental screening.
- **Input:** fixed complete reaction, matching label dictionary/configuration,
  Top-K, and explicit temperature-support status.
- **Output:** complete ranked condition sets, raw/decoded labels, checkpoint
  provenance, and validation state.

### Technique and implementation

Use Parrot conditionally. The code, data labels, environments, and CLI are
public, but the downloader's external checkpoint archives do not separately
declare machine-readable license terms, so organizational deployment requires
terms review. USPTO and Reaxys configurations do not have identical temperature
capabilities. The Skill is `reaction-condition-recommendation`.

### Independent metrics

Component Top-K recall, complete-set exact/similarity, temperature MAE where
supported, decoding failures, OOV/abstention, and agreement with exact
literature or ELN records.

### Hard constraints

Freeze the reaction first; bind dictionaries to checkpoints; never fabricate
unsupported temperature; keep LLM suggestions distinct from Parrot output; do
not claim deployment approval before checkpoint-terms review.

## Problem 6. Reaction-yield estimation

### Science query

For a fully specified reactant/reagent/product context, what isolated yield is
predicted and is that number trustworthy in the deployment domain?

### Goal, input, and output

- **Goal:** rank comparable in-domain reactions and identify steps requiring
  experiments or domain fine-tuning.
- **Input:** reactants, reagents/conditions, product, and frozen base or
  domain-fine-tuned checkpoint.
- **Output:** raw and display yield, matched/uncertain/OOD state, missing-input
  flags, validated uncertainty only when available, and provenance.

### Technique and implementation

Use `sagawa/ReactionT5v2-yield` through its official regression wrapper. Its
reported benchmarks do not establish arbitrary-chemistry error. Without a
deployment-matched held-out set, MAE/RMSE, and calibration, label output
`screening_only`. The Skill is `reaction-yield-estimation`.

### Independent metrics

MAE, RMSE, supplementary R2/Spearman, class/scale/time-split errors, uncertainty
coverage where implemented, OOD abstention, and out-of-range raw predictions.

### Hard constraints

No quantitative interpretation with missing reaction fields; prevent near-
duplicate leakage; do not extrapolate across labs/scales/classes without
validation; preserve raw values outside 0–100; never multiply step yields into
a route-success probability; an LLM cannot invent uncertainty intervals.

## Coverage of complete planning

- **Narrow retrosynthesis planning:** P1 + P2 contain the necessary model
  questions: expansion and search.
- **Model-based route review:** add P3 + P4 for centres and forward agreement.
- **Experimental hypothesis support:** add P5 + P6 for conditions and bounded
  yield screening.

This is not molecule-to-factory completeness. Procurement, dated supply,
EHS/calorimetry, work-up, purification, analytical release, scale-up, equipment,
mass balance, sustainability, patent/FTO, internal ELN knowledge, and laboratory
closed loops remain database, rule, experiment, process, and decision problems.

## Automation status

| Problem | Offline interface test | Live inference | Scientific benchmark | Status |
| --- | --- | --- | --- | --- |
| P1 | yes | RetroChimera env/weights | public benchmark required | integrated; no default-CI weight download |
| P2 | yes | AiZynthFinder assets/stock | frozen search benchmark | integrated; live search optional |
| P3 | Skill discovery | RXNMapper env | mapping benchmark | recipe complete |
| P4 | Skill discovery | ReactionT5v2 weights | forward benchmark | recipe complete |
| P5 | Skill discovery | Parrot env/terms review | fixed condition set | conditional recipe complete |
| P6 | Skill discovery | ReactionT5v2 weights | deployment held-out set | recipe complete; bounded interpretation |

## Scenario-wide hard constraints

1. **Ground-truth isolation:** each task sees only declared inputs; references
   are evaluator-only after outputs are fixed.
2. **Task isolation:** freeze other-task inputs/models when benchmarking one
   problem.
3. **Checkpoint freeze:** record model ID, revision, hash, training set,
   license, dependencies, and inference parameters.
4. **Representation consistency:** freeze parsing, canonicalization, salt/
   tautomer/stereo, and unordered-component rules.
5. **Search fairness:** freeze P2 policies, stock, budgets, constraints, and
   stopping rules; preserve timeouts and unsolved outputs.
6. **Held-out isolation:** intended products, true conditions, and yields cannot
   guide generation, beam choice, reranking, or tuning.
7. **No self-confirmation:** disclose shared data/model families; round trips
   are not experiments.
8. **Score-semantics isolation:** never collapse mapping confidence, beam score,
   planner score, product rank, condition score, and yield error into one
   uncalibrated confidence.
9. **Evidence isolation:** distinguish model, deterministic calculation,
   literature, ELN, vendor, expert, and LLM sources.
10. **Domain and abstention:** return unknown/OOD/screening-only when inputs,
    applicability, or calibration are insufficient.
11. **No hidden model download:** keep weights out of git; require explicit
    authorization, source/hash verification, and isolated loading.
12. **Final evaluation constraint:** evaluator results cannot feed back into
    models, filters, budgets, or ranking weights.

## Skill directories in this checkout

| Problem | Absolute directory |
| --- | --- |
| P1 | `/aaa/fionafyang/buddy1/whaleywang/OpenAI4S/skills/single-step-retrosynthesis/` |
| P2 | `/aaa/fionafyang/buddy1/whaleywang/OpenAI4S/skills/retrosynthesis_planning/` |
| P3 | `/aaa/fionafyang/buddy1/whaleywang/OpenAI4S/skills/reaction-atom-mapping/` |
| P4 | `/aaa/fionafyang/buddy1/whaleywang/OpenAI4S/skills/reaction-forward-prediction/` |
| P5 | `/aaa/fionafyang/buddy1/whaleywang/OpenAI4S/skills/reaction-condition-recommendation/` |
| P6 | `/aaa/fionafyang/buddy1/whaleywang/OpenAI4S/skills/reaction-yield-estimation/` |

See `MODEL_TASKS.md` for model-admission evidence, alternatives, and exclusions,
and [`scenarios/README.md`](scenarios/README.md) for the detailed benchmark
drafts.
