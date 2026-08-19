# Retrosynthesis Domain Decomposition: From Disconnection Prediction to Reviewable Route Decisions

## Scenario overview

“Generate a synthesis route for this target” is not one scientific problem. It contains several related but independently testable questions: where to disconnect, which precursors to propose, how to search recursively to available materials, whether a route is plausible, which conditions may work, and how to compare competing routes.

An end-to-end answer that is judged only by whether the final route looks reasonable cannot locate failure in the single-step model, tree search, stock definition, validator, or ranking logic. It also allows fluent LLM prose to hide a problem that the chemistry backend did not solve.

This scenario therefore defines eight domain subproblems rather than one fixed pipeline. Each subproblem has its own science query, input, output, executable solution, metrics, and failure boundary. A task may evaluate one subproblem or compose several. Downstream interpretation must not rewrite upstream deterministic results.

## Problem map

```text
                           Target SMILES
                                │
             ┌──────────────────┼──────────────────┐
             ▼                  ▼                  ▼
 Q1 Disconnection sites   Q2 Single-step       User constraints /
                           precursors            fixed stock
             │                  │                  │
             └──────────┬───────┘                  │
                        ▼                          │
                 Q3 Multi-step search ◀────────────┘
                        │
               ┌────────┴────────┐
               ▼                 ▼
        Q4 Stock termination  Q5 Feasibility validation
               │                 │
               └────────┬────────┘
                        ▼
          Q6 Conditions, selectivity, and risk
                        │
                        ▼
             Q7 Ranking and route diversity
                        │
                        ▼
          Q8 Evidence, confidence, and reporting

Q8's evidence and uncertainty boundary applies to Q1–Q7; it is not merely a
disclaimer appended at the end.
```

## Subproblem summary

| Subproblem | Core question | Primary output | Independently testable |
| --- | --- | --- | --- |
| Q1. Disconnection prediction | Which bonds should be disconnected first? | Ranked reaction centers / bond sets | Yes |
| Q2. Single-step precursor generation | Which precursor sets can produce the target in one step? | Top-K precursor sets and reaction hypotheses | Yes |
| Q3. Multi-step route search | How can single-step proposals reach available materials recursively? | AND-OR route trees | Yes |
| Q4. Stock termination | Are route leaves truly in the permitted stock? | Stock matches and unresolved leaves | Yes |
| Q5. Feasibility validation | Is each route structurally and chemically coherent? | Errors, warnings, and plausibility evidence | Yes |
| Q6. Conditions, selectivity, and risk | How might each step be run and what can fail? | Condition candidates and risk evidence | Yes |
| Q7. Ranking and diversity | Which routes are preferable and genuinely distinct? | Pareto ranking and diverse Top-N set | Yes |
| Q8. Evidence, confidence, and reporting | Which claims are predictions, evidence, or hypotheses? | Provenance, uncertainty, and review report | Yes |

## Q1. Reaction-center and disconnection prediction

### Science query

Given a target molecule without reference-route access, identify one or more bonds worth prioritizing for retrosynthetic disconnection.

### Input and output

- **Input:** Canonical target SMILES and optional protected-group, forbidden-bond, stereochemistry, or disconnection-count constraints.
- **Output:** Ranked bond sets containing atom indices, bond types, model score, policy source, and constraint status.

### Executable solution

Parse and canonicalize the molecule with RDKit, obtain Top-K centers from a template classifier, graph model, or bond differences inferred from single-step outputs, merge duplicate centers while preserving model disagreement, and apply explicit hard constraints. Return candidates rather than declaring one center chemically correct; Q2, Q3, and Q5 provide separate tests.

### Metrics

Top-K reaction-center recall, bond precision/recall/F1, hidden-reference disconnection match, deduplication rate, cross-model agreement, and constraint violations.

### Current capability and gap

RetroChimera/Syntheseus proposals and AiZynthFinder policies indirectly carry disconnection information. The repository does not yet expose reaction centers as a stable public result; it needs an atom-mapping/bond-diff adapter and evaluator.

## Q2. Single-step retrosynthetic precursor generation

### Science query

Given a target molecule or specified reaction center, generate precursor sets capable of reaching the product in one reaction.

### Input and output

- **Input:** Target SMILES, optional reaction center, model identity, and Top-K.
- **Output:** Ranked precursor sets, reaction SMILES, score and score type, and model provenance.

### Executable solution

Invoke an AiZynthFinder expansion policy or isolated `SyntheseusBackend`, canonicalize and validate every precursor, deduplicate unordered precursor sets, retain raw score semantics, validate mapped reactions and metadata, and optionally add an independent forward-model round-trip score. Missing data stays unknown; an LLM cannot fabricate it.

### Metrics

Top-K exact precursor-set accuracy, canonical precursor recall, reaction-center consistency, invalid/duplicate/empty prediction rates, forward round-trip success, latency, and predictions per target.

### Current capability and gap

The repository implements versioned external requests/responses, checkpoint provenance, Top-K normalization, and structured errors. It still needs an independent forward backend and live scientific-accuracy benchmarks; response replay currently verifies engineering contracts, not chemistry accuracy.

## Q3. Multi-step route search and composition

### Science query

How can single-step proposals be recursively composed into complete routes from the target to permitted starting materials?

### Input and output

- **Input:** Target, expansion and filter policies, stock, search budget, and user constraints.
- **Output:** AND-OR route trees with solved state, depth, leaves, search source, and original rank.

### Executable solution

Use AiZynthFinder's configured tree search. Expand molecule nodes with a single-step policy, preserve the AND semantics of each reaction's precursor set, prune with declared filters, bound cycles/depth/iterations/time, and save raw exports and checkpoints. Partial `solved=False` trees remain diagnostic output and are never presented as complete routes.

### Metrics

Solved-target rate, Top-N route success, node/expansion count, depth, wall time, peak memory, reference tree/reaction/intermediate similarity, replay consistency, and timeout rate.

### Current capability and gap

The repository safely constructs `aizynthcli` commands and normalizes exported route trees. Real search remains in an optional AiZynthFinder environment; offline CI fixtures do not claim live model-search performance.

## Q4. Purchasable-material and stock termination

### Science query

Are route leaves genuinely members of the task's permitted stock, and which leaves remain unresolved?

### Input and output

- **Input:** Route leaves, frozen stock snapshot, and declared salt/tautomer/stereochemistry matching policy.
- **Output:** Per-leaf `in_stock`, matched record, matching rule, stock version, and unresolved reason.

### Executable solution

Freeze and hash the stock before the run, perform exact canonical-SMILES matching first, treat salt stripping and tautomer normalization as explicit later tiers, separate fixed-stock membership from time-sensitive supplier search, and record the exact rule that produced every match. An LLM cannot label an unmatched leaf purchasable.

### Metrics

Stock-membership precision/recall, fully terminated route rate, unresolved-leaf count, per-tier match rates, and stock/match provenance completeness.

### Current capability and gap

AiZynthFinder provides search-time stock termination and reports starting materials. A stable per-leaf stock-match schema and snapshot digest still need to be surfaced for auditability.

## Q5. Structural integrity and reaction feasibility validation

### Science query

Is a candidate route structurally coherent, and does it contain invalid molecules, missing reactants, impossible atom changes, or weak reaction steps?

### Input and output

- **Input:** Normalized route tree, reaction SMILES/templates, optional forward model, and literature evidence.
- **Output:** Deterministic errors/warnings, forward evidence, precedent status, and explicitly unvalidated items.

### Executable solution

Run deterministic checks first, then atom-mapping/conservation checks when available, optionally perform an independent forward round trip, and retrieve exact or similarity-based precedents. Keep structural audit, forward prediction, and literature evidence as separate signals; combining them into a fake universal feasibility probability is prohibited.

### Metrics

Structural error/warning count, mapping/conservation pass rate, forward rank/score, precedent coverage, classification precision/recall/AUROC against evaluator labels, and calibration error only for genuinely calibrated validators.

### Current capability and gap

`structural_audit.py` already checks tree integrity, SMILES, reaction children, duplicate precursors, missing identity, and simple elemental deficits. It is explicitly not a forward model. A deployable forward predictor, atom mapper, and reaction-evidence evaluator remain necessary.

## Q6. Reaction conditions, selectivity, safety, and scale-up risk

### Science query

For a fixed reaction step, which reagents, solvents, temperatures, and times are worth testing, and what selectivity, safety, or scale-up failures are plausible?

### Input and output

- **Input:** Reactants, product, center, optional literature/ELN, and optional condition model.
- **Output:** Top-K condition candidates, evidence, applicability caveats, selectivity risks, safety notes, and validation experiments.

### Executable solution

Prefer exact or close precedents, optionally invoke a dedicated condition predictor, preserve several candidates, use an LLM only to organize evidence and explicit hypotheses, separately assess functional-group compatibility and selectivity, and return unknown when no support exists. Fabricated yields are forbidden.

### Metrics

Top-K reagent/solvent/catalyst/temperature recall, condition-set similarity, temperature error, selectivity-risk recall, hazardous-reagent recall, evidence-backed condition rate, and correct abstention rate.

### Current capability and gap

The Skill can retrieve evidence through guarded Host calls and generate explicitly labelled LLM hypotheses. It does not contain a validated condition-prediction backend, so fluent condition text is not a scientific solution to this subproblem.

## Q7. Route ranking, deduplication, and diversity decisions

### Science query

Given several candidate routes, which should be reviewed first, and which represent genuinely different chemical strategies?

### Input and output

- **Input:** Routes, audit findings, stock matches, user constraints, and optional cost/evidence/condition data.
- **Output:** Multi-objective/Pareto ranking, duplicate provenance, and a diverse Top-N set.

### Executable solution

Apply hard constraints before scores, rank using solved state, steps, stock completion, audit risk, evidence, cost, and search source, merge exact route signatures while preserving `duplicate_count` and `source_ranks`, and select diversity over reaction/product/precursor/leaf features. Weights remain evaluator-independent.

### Metrics

Valid/solved/stock-complete fraction in Top-N, NDCG/Spearman/pairwise agreement with chemists, duplicate-removal rate, feature coverage, maximum route similarity, Pareto coverage, and ranking stability under small weight perturbations.

### Current capability and gap

`workflow.py` and `route_review.py` implement deterministic ranking, stable-signature deduplication, Jaccard diversity, and `diversity_relaxed`. This is not yet a chemist-calibrated industrial utility function; cost, yield, equipment, and organizational constraints require separate data.

## Q8. Evidence, confidence, failure diagnosis, and review output

### Science query

How can a reviewer distinguish backend predictions, deterministic calculations, external evidence, and LLM hypotheses—and receive an honest failure when a subproblem is unsolved?

### Input and output

- **Input:** Q1–Q7 outputs, manifests, raw route exports, retrieved sources, and run logs.
- **Output:** Provenance-complete structured JSON, HTML/Markdown review artifacts, uncertainty boundaries, and failure reasons.

### Executable solution

Label every result by source type, record model/checkpoint/runtime identity, preserve retrieval URL/request/time/digest, render merged molecule nodes without losing route AND-OR semantics or duplicate provenance, and return structured failure/unknown states for missing checkpoints, timeouts, unsolved searches, or absent evidence.

### Metrics

Provenance completeness, replay and normalized-digest consistency, source-label accuracy, failure-classification accuracy, appropriate abstention rate, and unsupported-claim count.

### Current capability and gap

The repository implements path-free manifests, checkpoint hashes, versioned responses, structured backend errors, route dashboards, and Markdown reports. A task-level schema still needs to unify independent Q1–Q7 confidence and evaluator results.

## Recommended task compositions

The subproblems do not have to run as one pipeline:

| Task | Subproblems | Boundary |
| --- | --- | --- |
| Single-step benchmark | Q1 + Q2 + Q5 + Q8 | Evaluates disconnections and precursors, not complete routes |
| Multi-step benchmark | Q2 + Q3 + Q4 + Q5 + Q8 | Evaluates reaching one frozen stock |
| Route-selection benchmark | Q5 + Q7 + Q8 | Uses fixed candidate routes so search quality cannot leak into ranking |
| Condition benchmark | Q5 + Q6 + Q8 | Uses fixed reactions so planner quality cannot leak into conditions |
| End-to-end chemist review | Q1–Q8 | Produces routes and evidence but still does not establish experimental success |

## Automation status

```text
Q1 disconnection prediction             △ adapter/evaluator needed
Q2 single-step precursor generation     ✓ protocol exists; live accuracy needed
Q3 multi-step search                    ✓ optional AiZynthFinder environment/assets
Q4 stock termination                    △ search works; per-leaf provenance needed
Q5 deterministic structural audit       ✓
Q5 forward/literature feasibility       △ independent backends/evaluator needed
Q6 conditions and selectivity            △ evidence + LLM hypotheses today
Q7 ranking, deduplication, diversity     ✓ baseline capability implemented
Q8 provenance and review output         ✓ baseline capability implemented
Experimental success/yield/scale-up     ✗ expert and laboratory confirmation
```

## Unified hard constraints

1. **Ground-truth isolation:** Reference routes, true precursors/conditions, expert disconnections, and evaluator scores are evaluator-only.
2. **Subproblem isolation:** A subproblem receives fixed inputs; better upstream search cannot secretly inflate a condition or ranking benchmark.
3. **Fixed target and stock:** Canonicalization, stock snapshot, salt/tautomer, and stereochemistry rules are declared in advance.
4. **Backend score separation:** Raw scores from different models are not directly comparable probabilities or experimental success rates.
5. **Route-source integrity:** Route nodes come from declared backends; an LLM cannot invent or repair them and relabel the result.
6. **Audit before interpretation:** Deterministic errors and warnings survive later language-model interpretation.
7. **Stock claim constraint:** Only frozen-stock matches are labelled in-stock; supplier pages are timestamped external evidence.
8. **Evidence separation:** Backend output, deterministic computation, external evidence, and LLM hypotheses carry distinct labels.
9. **No fabricated conditions:** Without a condition model or evidence, return unknown or hypothesis—never invented yields or operating facts.
10. **Deduplication transparency:** Merged routes retain signatures, counts, and sources; restored similar routes expose `diversity_relaxed`.
11. **Model provenance:** External calls record model/version, checkpoint ID/SHA-256, training data, runtime packages, and failures.
12. **Failure honesty:** An unsolved subproblem returns structured failure rather than being disguised by downstream prose.
