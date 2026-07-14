# Catalyst SAR Screening Skill

This progressive-disclosure Skill defines a hard-locked single-atom-catalyst SAR pipeline: graphene M–N4 structures are evaluated only with FAIRChem UMA `uma-s-1p1`/`oc20`, then ranked and reported. The lock forbids silently substituting tables, heuristics, or another MLIP when UMA, Hugging Face access, or credentials are unavailable.

The Python sidecar contains real pipeline code, but a successful user result still requires compatible scientific packages, the model/weights, `HF_TOKEN` or reachable hub as applicable, compute, and a fresh work directory. The committed `metal_center_dissolution_*` files are deliberately stripped developer demo shells and must never be returned as live results.

## Direct files

| File | Responsibility |
| --- | --- |
| [`.gitignore`](.gitignore) | Excludes Python build/cache files and all image formats from the Skill root so generated live figures cannot be mistaken for committed user deliverables. |
| [`SKILL.md`](SKILL.md) | Main hard-lock recipe: required UMA backend/environment, readiness stop-and-ask behavior, fresh-workdir `run_pipeline` call, fixed stages, deliverable rules, developer-demo warning, and analyst checklist. |
| [`kernel.py`](kernel.py) | Optional sidecar: loads the structure catalog; parses descriptions and constructs/substitutes POSCARs; exposes UMA-only `CalculationTools`; checks dependencies/hub/model readiness; evaluates dissolution/ORR metrics; ranks and analyzes SAR; parses structures; renders figures/dashboard/report; and composes the end-to-end `run_pipeline` plus a constrained dissolution case helper. |
| [`contcar_catalog.json`](contcar_catalog.json) | Version-2 synthetic fixture/catalog of 28 embedded graphene/pyridineN M–N4 slab POSCAR texts used as exact or nearest structure templates; explicitly not an experimental dataset release. |
| [`build_example.py`](build_example.py) | Regenerates only text/HTML developer demo shells, strips numerical results/backend fields and image paths, sets disclaimers, and never runs UMA. |
| [`metal_center_dissolution_descriptions.json`](metal_center_dissolution_descriptions.json) | Three demonstration structure requests—Mn-N4, Fe-N4, and Cu-N4—used to illustrate input shape. |
| [`metal_center_dissolution_summary.json`](metal_center_dissolution_summary.json) | Sanitized three-row dissolution-mode demo metadata with no converged numerical predictions, marked `demo: true` and not a user deliverable. |
| [`metal_center_dissolution_dashboard.html`](metal_center_dissolution_dashboard.html) | Generated self-contained demo-shell dashboard containing disclaimers and no live UMA figures/metrics. |
| [`metal_center_dissolution_report.md`](metal_center_dissolution_report.md) | Generated demo-shell methods/report text that explicitly contains no computed candidate result. |

## Direct subdirectories

None.
