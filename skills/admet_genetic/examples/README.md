# ADMET Genetic Example

This directory is a committed, regenerable demonstration of the data contracts and reporting path. It is not evidence that ADMET-AI or a GA ran in the current environment, and its molecules/scores are not user-specific recommendations.

## Direct files

| File | Responsibility |
| --- | --- |
| [`build_example.py`](build_example.py) | Stdlib-oriented rebuild script: reads and validates committed CSV/config records, checks lineage/filter/scoring consistency, selects passing children that improve on their best ancestral seed, writes final candidates/report, and regenerates the dashboard through the parent sidecar. |
| [`seed_molecules.csv`](seed_molecules.csv) | Twelve demonstration seed IDs and SMILES forming generation zero/input identity. |
| [`config.yaml`](config.yaml) | Demonstration hard filters, score weights/transforms, property windows, ADMET risk threshold, and positive/negative endpoint keywords. |
| [`generation_log.csv`](generation_log.csv) | Recorded 108-row candidate ledger with generation, parents, operation detail, status, molecular properties, ADMET payload/flags, scores, and filter decisions. |
| [`generation_summary.csv`](generation_summary.csv) | Four-generation aggregate counts, best/mean score, pass count, and population best used by the report/dashboard. |
| [`candidates_final.csv`](candidates_final.csv) | Four derived passing child candidates selected for improvement over their ancestral seed, including baseline IDs/scores and deltas. |
| [`optimization_dashboard.html`](optimization_dashboard.html) | Self-contained generated visualization of generation history, scores, filters, lineage, and selected molecules. |
| [`report.md`](report.md) | Generated human-readable run overview, configuration, generation summary, candidate table, interpretation, and limitations. |

## Direct subdirectories

None.

Regeneration should be deterministic from the committed records; changing scientific values requires reviewing their provenance, not merely rebuilding presentation files.
