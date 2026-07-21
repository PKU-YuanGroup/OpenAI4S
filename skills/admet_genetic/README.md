# ADMET Genetic Optimization Skill

Optimizing molecules from seed SMILES under ADMET guidance, with an auditable lineage from every candidate back to the seed it came from. The Python sidecar carries the reusable parts: SMILES normalization, the scoring contract, lineage validation, and visualization. It stops short of a fixed genetic algorithm on purpose, and it never validates candidate chemistry experimentally.

RDKit, pandas, matplotlib, ADMET-AI, PyTorch and the model assets are all optional, and have to be installed into a selected environment before any of this runs. The rest is the agent's job: read the data contracts, build the mutation, crossover and selection logic, keep the lineage intact, and treat every prediction as triage evidence rather than fact.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The main recipe: prerequisites, seed normalization, the contracts that must be read first, how to assemble the GA, ADMET/SA/QED/property scoring, filters, diversity, lineage, outputs, reporting, and the limitations to state in the report. |
| [`kernel.py`](kernel.py) | The optional sidecar. It standardizes and canonicalizes SMILES, classifies ADMET-AI endpoints and aggregates them into a score plus risk flags, emits the canonical `operation_detail` JSON, and checks a generation log against the lineage contract. `render_optimization_history` turns that log into a self-contained dashboard, with RDKit molecule SVGs and matplotlib plots when those libraries are present. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | A committed, reproducible demonstration: inputs, recorded generations, the selections derived from them, a report and a dashboard. It is a fixture, not a live optimization result. |
| [`references/`](references/) | ADMET runtime notes, the data-contract and lineage rules, and GA design notes, read on demand through progressive disclosure. |
