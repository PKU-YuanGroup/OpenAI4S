# Retrosynthesis Planning Skill

Taking a target SMILES through a route search and then through a chemist's review of what comes back. AiZynthFinder does the searching, in an environment of its own. The sidecar here is stdlib-first: it normalizes and ranks the exported routes, removes duplicate route hypotheses, prefers a chemically broader review set, performs deterministic structural checks, and renders the review artifacts. RDKit, when it is installed, adds real structure depictions and molecule-level validation; Host LLM calls, when they are used, add the chemistry annotations.

The workflow supports planning and chemist triage, not experimental validation of a route. Conditions, yields, availability, safety notes and everything the LLM writes remain hypotheses until they are checked against the literature, ELN and vendor data, and expert review. The structural audit catches malformed route trees and suspicious atom coverage; it is not a forward-reaction model and does not establish feasibility.

## Recommended review path

Use [`workflow.py`](workflow.py) for the user-facing orchestration layer and keep [`kernel.py`](kernel.py) for the lower-level normalization, evidence, rendering and reporting primitives.

```python
from retrosynthesis_planning.kernel import load_aizynth_routes
from retrosynthesis_planning.workflow import (
    AiZynthSearchSpec,
    audit_routes,
    build_aizynth_search_command,
    prepare_routes,
)

search = AiZynthSearchSpec(
    policies=("uspto", "ringbreaker"),
    filters=("quick_filter",),
    stocks=("internal", "zinc"),
    cluster=True,
    nproc=4,
    checkpoint_path="checkpoint.json.gz",
)

command = build_aizynth_search_command(
    "CC(=O)Oc1ccccc1C(=O)O",
    "config.yml",
    output_path="aspirin_routes.json",
    conda_env="retro",
    search=search,
)

payload = load_aizynth_routes("aspirin_routes.json")
routes = prepare_routes(
    payload,
    max_routes=10,
    similarity_threshold=0.85,
    constraints={"require_solved": True},
)
audit = audit_routes(routes)
```

`AiZynthSearchSpec` exposes the documented `aizynthcli` switches for policy, filter, stock, clustering, multiprocessing, checkpoints and pre/post-processing modules. Search algorithm, depth, rewards and bond constraints still belong in AiZynthFinder's `config.yml`; the wrapper does not silently rewrite that configuration.

`extra_args` is the escape hatch for switches the class does not model. It is emitted ahead of the typed switches and must begin with a switch of its own, because `--policy`, `--filter`, `--stocks` and `--post_processing` are variadic and would otherwise absorb a bare value that followed them. It may neither repeat nor abbreviate a switch the wrapper already manages — `aizynthcli` builds its parser with argparse's default `allow_abbrev=True`, so blocking `--output` while allowing `--out` would block nothing — and so it cannot quietly rewrite the target, config or output path. `kernel.build_aizynth_command` enforces the same rule for the three switches it owns, so a caller who reaches it directly is covered too.

`prepare_routes(...)` applies the existing ranking path, collapses identical route trees while retaining their original ranks, and then selects a diverse review set using reaction/product/precursor features. When a fixed dashboard size requires a similar route to be reintroduced, it is marked with `diversity_relaxed=True` rather than being presented as independent evidence.

`audit_routes(...)` runs before LLM annotation. It reports missing trees, empty or invalid molecule SMILES, reactions without precursor children, duplicate precursors, missing reaction identity and — when RDKit is installed — simple product-versus-precursor elemental deficits. Every result carries an explicit disclaimer because these checks do not replace forward prediction, literature precedent or experimental review.

## Optional external models

[`external_backends.py`](external_backends.py) adds a versioned, stdlib-only subprocess boundary for optional single-step models. [`syntheseus_worker.py`](syntheseus_worker.py) can run RetroChimera or supported Syntheseus wrappers inside a separate Python or conda environment, keeping PyTorch, CUDA, checkpoints and model-specific dependencies out of the OpenAI4S core process.

[`model_deployment.py`](model_deployment.py) makes RetroChimera checkpoint setup reproducible without adding a package dependency: it records the three public upstream archives, routes optional downloads through the guarded Host capability, validates the reviewed byte count and MD5 plus a locally computed SHA-256, rejects unsafe ZIP members, extracts through an atomic staging directory, and writes the path-free manifest consumed by the backend.

Automatic checkpoint downloading is disabled by default. A model run can carry a path-free manifest containing model version, checkpoint identifier and SHA-256, training dataset and license information. Returned model scores remain raw model outputs and are always accompanied by a scientific disclaimer; they are not converted into experimental success probabilities.

See [`MODEL_BACKENDS.md`](MODEL_BACKENDS.md) and [`MODEL_BACKENDS_zh.md`](MODEL_BACKENDS_zh.md) for installation, manifest, protocol, failure handling and Harness replay details.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The pipeline end to end: the inputs to supply (target SMILES, an AiZynthFinder `config.yml`, a workdir), how the search is invoked and its JSON export loaded, and the ranking that follows. Then the molecule briefs for target, intermediates, stock precursors and unresolved terminal precursors; `host.llm` annotations; the self-contained HTML dashboard and Markdown analyst report; and the evidence boundary that keeps model-written conditions, yields and verdicts hypothetical. |
| [`kernel.py`](kernel.py) | The lower-level sidecar. It canonicalizes SMILES when RDKit is present, builds a safe AiZynth command, loads route exports, normalizes and ranks them, collects molecule and reaction evidence, calls the Host LLM when requested, and renders the dashboard and Markdown report. |
| [`workflow.py`](workflow.py) | The user-facing orchestration layer: validated `aizynthcli` options and the normalize → rank → de-duplicate → diversify review path. |
| [`route_review.py`](route_review.py) | Stable route signatures, duplicate provenance and diversity-aware route selection based on reaction, product, precursor and terminal-material features. |
| [`structural_audit.py`](structural_audit.py) | Deterministic route-tree checks before LLM interpretation. It remains stdlib-only and adds RDKit parse/element checks only when RDKit is installed. |
| [`external_backends.py`](external_backends.py) | Versioned external-model request/response validation, path-free model manifests, timeout and size enforcement, and the `SyntheseusBackend` subprocess adapter. |
| [`model_deployment.py`](model_deployment.py) | Pure-stdlib RetroChimera checkpoint registry, guarded Host download, archive verification, safe atomic extraction and path-free manifest generation. |
| [`syntheseus_worker.py`](syntheseus_worker.py) | The isolated optional-dependency worker for RetroChimera and supported Syntheseus model classes. It moves descriptor 1 onto stderr before handling a request — so native model output cannot corrupt the protocol — strips filesystem paths out of model metadata, and emits one structured JSON response. |
| [`MODEL_BACKENDS.md`](MODEL_BACKENDS.md) | English guide to isolated model installation, provenance manifests, usage, wire errors, scientific limits and offline replay verification. |
| [`MODEL_BACKENDS_zh.md`](MODEL_BACKENDS_zh.md) | Chinese version of the external-model backend and trust guide. |
| [`SCENARIO.md`](SCENARIO.md) | Robust multi-strategy retrosynthesis scenario: blind strategy selection, bounded search, route audit, deduplication, diversity, evidence, evaluation metrics, and implementation constraints. |
| [`SCENARIO_zh.md`](SCENARIO_zh.md) | Chinese version of the robust multi-strategy retrosynthesis scenario and evaluation contract. |

Focused regressions for this layer live in [`../../tests/test_retrosynthesis_scoring_regressions.py`](../../tests/test_retrosynthesis_scoring_regressions.py) and [`../../tests/test_harness_contract.py`](../../tests/test_harness_contract.py).

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | The deterministic aspirin-shaped route and annotation fixtures, the HTML and report generated from them, and the script that rebuilds both. |
