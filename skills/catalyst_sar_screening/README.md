# Catalyst SAR Screening Skill

The progressive-disclosure recipe for single-atom-catalyst SAR screening, with the energy engine locked down. Graphene M–N4 structures are evaluated with FAIRChem UMA `uma-s-1p1` / `oc20` and with nothing else, then ranked and reported. When UMA, Hugging Face access or credentials are unavailable, the recipe stops and asks; quietly falling back to a lookup table, a heuristic, or a different MLIP is forbidden.

The Python sidecar holds real pipeline code, but a usable result still needs the scientific packages, the model weights, `HF_TOKEN` or a reachable hub as applicable, compute, and a fresh work directory. The committed `metal_center_dissolution_*` files are developer demo shells with the numbers deliberately stripped out, and must never be handed back as a live result.

Even on a clean run, remember what UMA is. `uma-s-1p1` on the `oc20` task is a machine-learned interatomic potential, a surrogate trained to approximate DFT energies rather than to solve for them, and every dissolution potential and ORR overpotential in the report is derived from its predicted binding energies. So the ranking this Skill produces is a triage order: which metal centers are worth a DFT calculation or a synthesis next. It is not a measurement of how stable or how active any of these catalysts really is.

## Files

| File | Responsibility |
| --- | --- |
| [`.gitignore`](.gitignore) | Keeps Python build and cache files, plus every image format, out of the Skill root, so a figure from a live run cannot be mistaken for a committed user deliverable. |
| [`SKILL.md`](SKILL.md) | The hard-lock recipe itself: the required UMA backend and environment, when to stop and ask instead of improvising, how to call `run_pipeline` into a fresh workdir, the fixed stages, what counts as a deliverable, the developer-demo warning, and the analyst checklist. |
| [`kernel.py`](kernel.py) | The optional sidecar, and where the pipeline actually lives. It loads the structure catalog, parses a description into a POSCAR (exact catalog match first, otherwise a derived one with the metal substituted), exposes the UMA-only `CalculationTools`, and checks dependencies, hub reachability and model readiness before anything runs. It then evaluates dissolution and ORR metrics, ranks candidates, analyzes SAR trends, parses structures back, and renders the figures, dashboard and report. `run_pipeline` composes the end-to-end chain; `run_metal_center_dissolution_case` is the constrained helper that pins the M–N4 motif and varies only the metal center. |
| [`contcar_catalog.json`](contcar_catalog.json) | Version 2 of the embedded catalog: 28 graphene / pyridineN M–N4 slab POSCAR texts, used as an exact template when one matches and as the nearest starting structure when none does. It is a synthetic fixture, explicitly not an experimental dataset release. |
| [`build_example.py`](build_example.py) | Rebuilds the text and HTML developer demo shells only. It strips numerical results, backend fields and image paths, sets the disclaimers, and never runs UMA. |
| [`metal_center_dissolution_descriptions.json`](metal_center_dissolution_descriptions.json) | Three demonstration structure requests (Mn-N4, Fe-N4, Cu-N4) that show the shape of the input. |
| [`metal_center_dissolution_summary.json`](metal_center_dissolution_summary.json) | Sanitized three-row demo metadata for the dissolution mode. It carries no converged numerical predictions, is marked `demo: true`, and is not a user deliverable. |
| [`metal_center_dissolution_dashboard.html`](metal_center_dissolution_dashboard.html) | The generated demo-shell dashboard: self-contained HTML carrying the disclaimers, with no live UMA figures or metrics in it. |
| [`metal_center_dissolution_report.md`](metal_center_dissolution_report.md) | The generated demo-shell methods and report text. It says outright that it contains no computed candidate result. |
