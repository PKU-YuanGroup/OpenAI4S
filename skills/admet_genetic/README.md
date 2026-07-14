# ADMET Genetic Optimization Skill

This progressive-disclosure Skill describes an ADMET-guided genetic molecule-optimization workflow from seed SMILES. Its Python sidecar supplies reusable normalization, scoring-contract, lineage, and visualization helpers, but deliberately does **not** implement a fixed genetic algorithm or validate candidate chemistry experimentally.

Optional RDKit, pandas, matplotlib, ADMET-AI, PyTorch, and model assets must be installed in a selected environment. The agent is responsible for reading the data contracts, constructing mutation/crossover/selection logic, preserving lineage, and treating predictions as triage evidence.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Main recipe for prerequisites, seed normalization, required contracts, GA assembly, ADMET/SA/QED/property scoring, filters, diversity, lineage, outputs, reporting, and limitations. |
| [`kernel.py`](kernel.py) | Optional sidecar: standardizes/canonicalizes SMILES; classifies and aggregates ADMET-AI endpoints; builds canonical operation-detail JSON; validates generation-log lineage; and renders a self-contained optimization-history dashboard with optional RDKit molecule SVGs and matplotlib plots. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | Reproducible committed demonstration inputs, recorded generations, derived selections, report, and dashboard; not a live optimization result. |
| [`references/`](references/) | On-demand ADMET runtime, data-contract/lineage, and GA design notes read through progressive disclosure. |
