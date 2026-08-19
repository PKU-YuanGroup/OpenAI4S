# Retrosynthesis Planning Scenario: Robust Multi-Route Planning When the Best Search Strategy Is Unknown

## Scenario overview

This scenario covers the case where a target molecule, a purchasable-stock snapshot, and several deployable retrosynthesis models or search strategies are available, but the best strategy for the target is unknown. Different single-step models, expansion policies, filters, search depths, and stock definitions may produce materially different routes. A single search followed by taking the backend's top score can mistake model preference, duplicated hypotheses, or malformed trees for a reliable answer.

Without exposing a reference synthesis or evaluator answer during planning, the workflow therefore searches a predeclared, bounded strategy portfolio under comparable budgets. It normalizes and audits every result, selects a robust strategy using solvability, stock termination, structural integrity, repeat stability, route diversity, and compute cost, and only then performs the final full-budget search. The final route pool is deduplicated, diversity-selected, enriched with traceable evidence, and rendered for chemist review.

This is route discovery and triage, not experimental validation. Model scores are not experimental success probabilities. Conditions, yields, selectivity, safety, and scale-up notes supplied by an LLM remain hypotheses requiring literature, ELN, expert, and experimental review.

## Scenario flow compared with the baseline

```text
Robust multi-strategy scenario                       Baseline

Target SMILES + fixed stock + backend assets         Target SMILES + one config
        │                                                  │
        ▼                                                  ▼
Normalize target and freeze input boundary             Run one search
        │                                                  │
        ▼                                                  ▼
Declare strategy portfolio and equal budgets           Sort by backend score
        │                                                  │
        ▼                                                  ▼
┌──── Loop over candidate strategies ────┐             Return Top-N routes
│                                         │
│ Select policy / filter / seed / budget  │
│        ↓                                │
│ Run bounded retrosynthesis search       │
│        ↓                                │
│ Normalize routes + structural audit     │
│        ↓                                │
│ Score solvability, stock termination,   │
│ stability, diversity, audit risk, cost  │
│        ↓                                │
│ Aggregate strategy diagnostics          │
└─────────────────────────────────────────┘
        │
        ▼
Select a robust lower-cost strategy
        │
        ▼
Run the final full-budget search
        │
        ▼
Merge and normalize the route pool
        │
        ▼
Deterministic audit + hard-constraint filtering
        │
        ▼
Rank + deduplicate + diversity-select
        │
        ▼
Evidence retrieval + explicitly labelled LLM hypotheses
        │
        ▼
Routes, starting materials, diagnostics, and review report
```

## Stages

### Science query

Given a target SMILES, a fixed purchasable-stock snapshot, and deployable retrosynthesis models or search strategies, automatically select a robust search strategy and generate structurally valid, stock-terminated, meaningfully distinct multi-step routes for chemist review without seeing a reference route, true conditions, or the evaluator's preferred configuration.

### Stage 1. Target validation and boundary freezing

**Goal:** Establish one reproducible target representation and freeze the inputs visible to the scenario.

**Input:** Target SMILES, AiZynthFinder configuration, stock files, permitted policies and filters, and optional model manifests.

**Output:** Canonical target, asset summary, stock identity, and run provenance.

**Method:** Parse and canonicalize the target with RDKit when available, reject empty or invalid structures, and record versions or digests for configurations, stocks, and checkpoint manifests. Every candidate strategy must reuse the same target and stock snapshot.

### Stage 2. Search portfolio and budget definition

**Goal:** Declare a chemically and operationally meaningful bounded strategy space before search begins.

**Input:** Expansion policies, filter policies, stocks, search algorithms, depth and iteration limits, seeds, and optional single-step backends.

**Output:** A bounded `search portfolio` with explicit parameters, seeds, and resource limits.

**Method:** AiZynthFinder owns multi-step tree search. RetroChimera or another Syntheseus wrapper may supply single-step precursor proposals but must not be presented as a complete multi-step planner. Candidate strategies receive equal or explicitly normalized expansion, wall-time, and concurrency budgets.

### Stage 3. Bounded multi-strategy search

**Goal:** Produce comparable routes and run diagnostics for each strategy without reference-route access.

**Input:** Canonical target, fixed stock, and `search portfolio`.

**Output:** Raw route exports, status, failure information, and resource use for every strategy and seed.

**Method:** Run `aizynthcli` for each bounded strategy and retain the raw JSON, optional checkpoints, exit state, timing, and errors. External single-step models use the versioned JSON boundary and report model version, checkpoint digest, training dataset, and runtime package versions.

### Stage 4. Route normalization

**Goal:** Convert heterogeneous search results into one comparable representation.

**Input:** Raw route JSON or versioned external-backend responses.

**Output:** A common route schema containing the tree, solved state, backend score, steps, starting materials, and source metadata.

**Method:** `normalize_routes(...)` standardizes shape while preserving strategy, seed, model, and original rank. Missing information remains unknown. Normalization never pretends that raw scores from different models are calibrated probabilities.

### Stage 5. Deterministic structural audit

**Goal:** Reject broken exports and expose obvious structural risks before LLM interpretation or final selection.

**Input:** Normalized route trees.

**Output:** Route-level errors, warnings, and an audit summary.

**Method:** Detect missing trees, missing or invalid molecule SMILES, reactions without precursors, duplicate precursors, missing reaction identity, and simple product-versus-precursor elemental deficits when RDKit is available. Errors may be hard filters. Warnings remain visible because this audit is not a forward model and cannot establish feasibility, conditions, yield, or selectivity.

### Stage 6. Blind strategy assessment and selection

**Goal:** Select a robust, affordable strategy without evaluator Ground Truth.

**Input:** Normalized routes, audit results, stock termination, repeated runs, and resource use.

**Output:** One selected strategy or a small Pareto set with an explainable decision record.

**Method:** Aggregate solved-route rate, complete stock termination, structurally valid-route rate, agreement across seeds, independent route count, route-feature coverage, shortest depth, unresolved leaves, runtime failures, and compute cost. Use predeclared weights or Pareto rules. Do not compare uncalibrated raw scores across backends. Prefer the cheaper, simpler, more stable strategy when outcomes are close.

### Stage 7. Final full-budget search and route-pool merge

**Goal:** Generate the final candidate pool using only the blindly selected strategy decision.

**Input:** Selected strategy, canonical target, fixed stock, and final budget.

**Output:** Final raw route pool with complete provenance.

**Method:** Increase to the final budget only after strategy selection. If several Pareto strategies are retained, run and merge them without using reference-route similarity to choose among them. Persist parameters, seeds, raw exports, and resource use.

### Stage 8. Route ranking, deduplication, and diversity selection

**Goal:** Select high-quality candidates representing distinct chemical ideas rather than repeated variants.

**Input:** Final normalized pool, audit results, user constraints, and optional reaction evidence.

**Output:** Ranked independent routes with duplicate and diversity diagnostics.

**Method:** Rank deterministically using solved status, constraints, audit penalties, backend ordering, steps, and starting materials. Merge exact duplicates using stable route signatures over reaction identity, products, precursors, and leaves while retaining `source_ranks` and `duplicate_count`. Apply Jaccard-based route-feature diversity selection. Any similar route restored to fill a display quota must carry `diversity_relaxed=True`.

### Stage 9. Evidence enrichment and hypothesis annotation

**Goal:** Add traceable evidence while separating observations from model-written hypotheses.

**Input:** Candidate routes and their targets, intermediates, starting materials, and reactions.

**Output:** Molecule briefs, literature or database sources, reaction interpretation, condition hypotheses, and risk notes.

**Method:** Retrieve PubChem, literature, and supplier records through guarded Host capabilities and store retrieval provenance. Normal AiZynthFinder exports do not predict conditions. Conditions require a separate predictor, literature evidence, or an explicitly uncertain LLM hypothesis. The LLM may explain a route but must not silently add, delete, or rewrite deterministic route-tree nodes.

### Stage 10. Route output and diagnosis

**Goal:** Produce a reviewable and rejectable decision package rather than one opaque "best route."

**Input:** Final routes, audits, evidence, provenance, and strategy diagnostics.

**Output:** Route trees, starting materials, rankings, audit findings, strategy diagnostics, HTML dashboard, and Markdown report.

**Method:** Report solved state, steps, stock termination, backend source, score type, duplicate provenance, diversity state, and audit issues for every route. Keep backend output, deterministic computation, external evidence, and LLM hypotheses visually distinct. Return structured failure diagnostics when no route satisfies the constraints.

## Automation feasibility

The workflow can be orchestrated in Python, but experimental feasibility cannot be established by computation alone:

```text
Target normalization                       ✓
Search command construction and loops      ✓
AiZynthFinder multi-step search             ✓ (optional environment/assets)
RetroChimera single-step proposals          ✓ (verified checkpoint required)
Route schema normalization                  ✓
Deterministic structural audit              ✓
Blind diagnostics and Pareto selection      ✓
Ranking, deduplication, diversity selection ✓
Evidence retrieval and provenance           ✓ (approved network required)
HTML / Markdown output                      ✓
Experimental conditions, yield, feasibility ✗ (literature/ELN/expert/lab required)
```

The repository already implements safe search-command construction, normalization and ranking, route deduplication and diversity selection, structural audit, the external single-step protocol, checkpoint provenance, and review rendering. The complete multi-strategy loop and blind strategy selector belong in a scenario orchestration layer with offline fixtures and separately marked live-backend tests.

## Evaluation metrics

Ground Truth is evaluator-only and becomes visible after the run:

1. **Reference-route recovery:** reaction steps and centers/templates, key intermediates, starting materials, and route-tree similarity.
2. **Route validity:** solved-route rate, complete stock termination, parseable SMILES, structural-error count, and unresolved leaves.
3. **Route quality:** step count, starting-material burden, constraint satisfaction, audit warnings, and forward or literature support when available.
4. **Route diversity:** independent route count, reaction-feature coverage, starting-material differences, and maximum pairwise similarity.
5. **Strategy robustness:** consistency of solved state, key disconnections, and terminal materials across seeds or bounded perturbations.
6. **Iteration and selection quality:** whether several predeclared strategies were truly compared, failures retained, lower-cost ties preferred, and the final run followed the blind decision.
7. **Evidence and provenance completeness:** traceability of model/checkpoint, configuration, stock, raw exports, external sources, and LLM hypotheses.
8. **Resource efficiency:** wall time, search expansions, peak memory, timeout rate, and compute per independent valid route.

## Hard implementation constraints

1. **Ground-truth isolation:** The agent cannot access reference routes, hidden templates or disconnections, or evaluator scores during search, selection, ranking, evidence retrieval, or reporting.
2. **Fixed target and stock:** All strategies use the identical canonical target and stock snapshot. Any tautomer, salt, or representation transform is declared before the run and applied uniformly.
3. **Predeclared search space:** Policies, filters, stocks, single-step backends, depths, iterations, and seeds are bounded before search. Evaluator feedback cannot trigger a favorable new strategy.
4. **Budget parity:** Strategy comparison uses equal or normalized time, expansions, and concurrency. Full-budget search starts only after blind selection.
5. **Backend score separation:** Raw scores from different models or policies are not directly comparable probabilities or experimental success rates.
6. **Route-source integrity:** Deterministic route trees come only from declared search backends or versioned model responses. An LLM cannot invent or repair steps and relabel them as backend output.
7. **Structural-audit ordering:** Audit runs before LLM annotation. Natural-language interpretation cannot suppress deterministic errors or warnings.
8. **Stock claim constraint:** Only fixed-stock matches may be labelled in-stock or purchasable. Supplier search is separate timestamped evidence.
9. **Deduplication transparency:** Merged routes retain signatures, duplicate counts, and source ranks. Reintroduced similar routes expose `diversity_relaxed`.
10. **Final-search constraint:** Final strategy and budget follow blind diagnostics; hidden Ground Truth cannot tune or cherry-pick the final routes.
11. **Evidence isolation:** Literature, database, ELN, and supplier claims retain source provenance. Retrieved text cannot alter configuration, stock, or evaluator logic.
12. **Hypothesis labelling:** LLM-derived conditions, yield, selectivity, safety, scale-up feasibility, and verdicts are explicit hypotheses with validation steps.
13. **Model provenance:** External calls record model/version, checkpoint ID and SHA-256, training dataset, runtime packages, and failures. Weights stay outside the Skill and repository.
14. **Failure honesty:** Missing checkpoints, unavailable backends, timeouts, unsolved searches, and hard-constraint rejection produce structured failures, never fabricated routes.
