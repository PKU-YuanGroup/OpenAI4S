# Evaluate Model Skill

This progressive-disclosure Skill supplies a held-out evaluation recipe plus pure-stdlib metric helpers. Loading the Skill may attach [`kernel.py`](kernel.py) to the persistent Python kernel; no model, predictions, split, or scientific conclusion is created automatically.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Defines leakage-aware evaluation, baseline/subgroup/uncertainty checks, binary/regression invocation, and the minimum reporting contract. |
| [`kernel.py`](kernel.py) | Optional sidecar: `binary_classification_metrics` computes confusion counts, accuracy, precision/recall/specificity/F1 and tie-aware ROC AUC; `regression_metrics` computes MAE/RMSE/bias/R²; `bootstrap_ci` returns a deterministic percentile interval for scalar observations. |

## Direct subdirectories

None.

These helpers summarize supplied observations; they do not verify that data are held out, independent, representative, or clinically meaningful.
