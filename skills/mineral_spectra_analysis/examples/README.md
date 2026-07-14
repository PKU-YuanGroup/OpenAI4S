# Mineral Spectra Example

This directory records one synthetic evaluation case. The blind-analysis summary and hidden answer key are committed separately so the report can demonstrate evaluation without allowing the inference loop to read truth.

## Direct files

| File | Responsibility |
| --- | --- |
| [`build_example.py`](build_example.py) | Stdlib-only report rebuilder that reads the committed analysis/components/truth JSON, formats predictions, diagnostics, truth metrics and iteration history, and does not rerun the optional scientific pipeline. |
| [`case1_analysis.json`](case1_analysis.json) | Recorded blind-pipeline configuration, three predicted mineral phases/fractions/support peaks, residual diagnostics, evaluation metrics, iteration history, and artifact names. |
| [`case1_components.json`](case1_components.json) | Human-readable synthetic-generation summary defining the three mineral phases, true fractions, RRUFF source role, noise/spike count, and baseline strength. |
| [`case1_mineral_spectra_report.md`](case1_mineral_spectra_report.md) | Generated report comparing blind predictions with hidden truth, including reliability diagnostics, evaluation metrics, iteration trace, and file roles. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`case1/`](case1/) | Observable spectrum/plot plus the separately stored hidden truth for the synthetic case. |

The perfect component F1 is a property of this recorded synthetic case, not a general performance claim.
