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

`prepare_routes(...)` applies the existing ranking path, collapses identical route trees while retaining their original ranks, and then selects a diverse review set using reaction/product/precursor features. When a fixed dashboard size requires a similar route to be reintroduced, it is marked with `diversity_relaxed=True` rather than being presented as independent evidence.

`audit_routes(...)` runs before LLM annotation. It reports missing trees, empty or invalid molecule SMILES, reactions without precursor children, duplicate precursors, missing reaction identity and — when RDKit is installed — simple product-versus-precursor elemental deficits. Every result carries an explicit disclaimer because these checks do not replace forward prediction, literature precedent or experimental review.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The pipeline end to end: the inputs to supply (target SMILES, an AiZynthFinder `config.yml`, a workdir), how the search is invoked and its JSON export loaded, and the ranking that follows. Then the molecule briefs for target, intermediates, stock precursors and unresolved terminal precursors; `host.llm` annotations; the self-contained HTML dashboard and Markdown analyst report; and the evidence boundary that keeps model-written conditions, yields and verdicts hypothetical. |
| [`kernel.py`](kernel.py) | The lower-level sidecar. It canonicalizes SMILES when RDKit is present, builds a safe AiZynth command, loads route exports, normalizes and ranks them, collects molecule and reaction evidence, calls the Host LLM when requested, and renders the dashboard and Markdown report. |
| [`workflow.py`](workflow.py) | The user-facing orchestration layer: validated `aizynthcli` options and the normalize → rank → de-duplicate → diversify review path. |
| [`route_review.py`](route_review.py) | Stable route signatures, duplicate provenance and diversity-aware route selection based on reaction, product, precursor and terminal-material features. |
| [`structural_audit.py`](structural_audit.py) | Deterministic route-tree checks before LLM interpretation. It remains stdlib-only and adds RDKit parse/element checks only when RDKit is installed. |

Focused regressions for this layer live in [`../../tests/test_retrosynthesis_workflow.py`](../../tests/test_retrosynthesis_workflow.py).

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | The deterministic aspirin-shaped route and annotation fixtures, the HTML and report generated from them, and the script that rebuilds both. |
