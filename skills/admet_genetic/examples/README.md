# ADMET Genetic Example

A committed, regenerable demonstration of the data contracts and the reporting path. It is not evidence that ADMET-AI or a GA ran in the current environment, and the molecules and scores in it are recommendations for nobody.

## Files

| File | Responsibility |
| --- | --- |
| [`build_example.py`](build_example.py) | The rebuild script, written against the stdlib as far as possible. It reads the committed CSV and config records, re-checks lineage, filters and scoring for consistency, picks the passing children that improve on their best ancestral seed, writes the final candidates and the report, and calls back into the parent sidecar to regenerate the dashboard. It does not run the GA or ADMET-AI. |
| [`seed_molecules.csv`](seed_molecules.csv) | Twelve demonstration seed IDs and SMILES. This is generation zero and the input identity everything else is traced back to. |
| [`config.yaml`](config.yaml) | The demonstration hard filters, score weights and transforms, property windows, ADMET risk threshold, and the positive/negative endpoint keywords. |
| [`generation_log.csv`](generation_log.csv) | The recorded candidate ledger, 108 rows: generation, parents, operation detail, status, molecular properties, the raw ADMET payload and derived flags, scores, and the filter decision for each row. |
| [`generation_summary.csv`](generation_summary.csv) | Per-generation aggregates over the four generations (counts, best and mean score, pass count, population best) as used by the report and the dashboard. |
| [`candidates_final.csv`](candidates_final.csv) | The four children that survive selection, each one strictly better than its ancestral seed, with the baseline seed IDs, the baseline score and the delta kept alongside. |
| [`optimization_dashboard.html`](optimization_dashboard.html) | The generated visualization: generation history, scores, filters, lineage and the selected molecules, in one self-contained HTML file. |
| [`report.md`](report.md) | The generated human-readable write-up: run overview, configuration, generation summary, candidate table, interpretation, and limitations. |

Regenerating from the committed records should be deterministic. If a scientific value changes, that is a signal to go and review where the number came from, not to rebuild the presentation files until they agree.
