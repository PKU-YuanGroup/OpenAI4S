---
name: evaluate-model
description: Evaluate binary classification or regression models with confusion-matrix metrics, tie-aware ROC AUC, regression errors, and deterministic bootstrap confidence intervals; emphasizes held-out data, uncertainty, baselines, and subgroup checks.
origin: openai4s
category: model-evaluation
---

# Evaluate a model

Use this skill when comparing predictive models, selecting a threshold, or
reporting held-out performance. Compute metrics only after defining the target,
unit of analysis, split boundary, baseline, and decision cost.

## Workflow

1. Confirm that evaluation examples and related entities were never used for
   fitting, preprocessing, feature selection, or threshold selection.
2. Choose a primary metric from the scientific or operational question; do not
   select it after inspecting test results.
3. Compare against a simple baseline and report uncertainty, not only a point
   estimate.
4. Inspect clinically or scientifically relevant subgroups and failure modes.
5. Keep probability quality, ranking quality, and thresholded decisions
   separate. Accuracy is rarely sufficient for an imbalanced target.
6. Save predictions with stable sample IDs so every aggregate is auditable.

## Import and run

```python
from importlib import import_module

metrics = import_module("evaluate-model.kernel")
classification = metrics.binary_classification_metrics(
    y_true, scores=probabilities, threshold=0.35
)
regression = metrics.regression_metrics(y_true_continuous, predictions)
interval = metrics.bootstrap_ci(per_sample_losses, resamples=2000, seed=42)
```

The binary helper reports the confusion matrix, accuracy, precision, recall,
specificity, F1, balanced accuracy, and tie-aware ROC AUC when scores are
provided. Undefined ratios are returned as `None`, not silently replaced by
zero.

## Reporting contract

State the split strategy, sample and group counts, prevalence, threshold source,
primary metric with interval, baseline, subgroup caveats, and all exclusions.
Bootstrap intervals describe sampling variability under the resampling unit;
they do not correct leakage, dataset shift, measurement error, or dependence
between observations. Use grouped or clustered resampling when rows are not
independent.
