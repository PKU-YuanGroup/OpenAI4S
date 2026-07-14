# Mineral Spectra Example

One recorded synthetic evaluation case. The blind-analysis summary and the hidden answer key are committed as separate files, so the report can show a comparison against truth without ever letting the inference loop read it.

## Files

| File | Responsibility |
| --- | --- |
| [`build_example.py`](build_example.py) | Rebuilds the example report from the committed analysis, components and truth JSON, using nothing but the standard library. It formats the predictions, diagnostics, truth metrics and iteration history; it does not rerun the optional scientific pipeline. |
| [`case1_analysis.json`](case1_analysis.json) | The recorded output of the blind pipeline: the config it ran with, three predicted mineral phases with fractions and supporting peaks, residual diagnostics, evaluation metrics, the iteration history, and the names of the artifacts the run refers to. |
| [`case1_components.json`](case1_components.json) | A readable summary of how the case was synthesized. The three mineral phases and their true fractions, the RRUFF reference spectrum each one came from, and the noise level, spike count and baseline strength used to dirty the mixture. |
| [`case1_mineral_spectra_report.md`](case1_mineral_spectra_report.md) | The generated report: blind predictions set against the hidden truth, with reliability diagnostics, evaluation metrics, the iteration trace, and what each file in the case is for. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`case1/`](case1/) | The observable spectrum and its plot, with the hidden truth stored alongside them but kept in its own file. |

The perfect component F1 belongs to this one recorded synthetic case. It is not a general performance claim.
