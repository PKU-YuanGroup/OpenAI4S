---
name: text-features
description: Turn free-text rows into calibrated numeric features with TypeSafe Jev, then model them on a leakage-safe development split. The main model proposes Noul/Score questions; host.judge answers every row; sklearn in the science extra fits and evaluates. Features keep their source question and measurement error and are not a human gold standard.
origin: openai4s
license: Apache-2.0
category: model-evaluation
capabilities:
  network:
    mode: host_only
    domains:
      - api.typesafe.ai
metadata:
  # Experimental text_features: user-selected data-row text is sent to
  # TypeSafe Jev at api.typesafe.ai when the capability is enabled in
  # Customize → Experimental. info_url. verified 2026-09-20
  third_party:
    - kind: service
      name: TypeSafe
      info_url: https://docs.typesafe.ai/
      privacy_url: https://typesafe.ai/legal/privacy-policy
# Modeling uses numpy, pandas, and scikit-learn from the science extra
# (`uv sync --extra science` / the prebuilt science env). Those packages
# are not core dependencies. The loader only knows how to check `gpu`, so
# the science-env requirement is stated here and in the recipe body rather
# than as unverifiable readiness tokens.
requirements: []
---

# Text features (experimental)

Use this skill when you have many free-text rows and a supervised target, and
you want calibrated numeric features instead of bag-of-words. The main model
proposes operationalizable questions; Jev answers each row; the sidecar fits a
model on a development split and scores the frozen test split once.

This is experimental. Turn on `text_features` under Customize → Experimental
(the master experimental-judgment switch must also be on). Headless: set
`OPENAI4S_EXPERIMENTAL_JUDGMENT=1` and `OPENAI4S_JUDGMENT_TEXT_FEATURES=1`.
When the capability is off, the helpers return `status: "disabled"` and do
not raise.

## When to use it

- Lots of free text (notes, abstracts, reports) plus a label or score.
- You need features a colleague can read: each column is a question, a
  probability or graded expectation, and a spread.
- You can hold out a test set and leave it untouched while questions change.

## When not to use it

- Small samples, or no supervised target — there is nothing to freeze against.
- Sensitive data. Selected row text is sent to `api.typesafe.ai`. The service
  is hosted in the United States. Do not enable this on clinical notes, secrets,
  or anything that must not leave the machine.
- You need a human gold standard. **Jev features are not a human gold
  standard.** They are calibrated model judgments of the text you sent, with
  measurement error. Do not treat a Noul probability as a verified fact.

## Data that leaves the machine

Every `featurize` call sends the user-selected row text in `state.text` to
TypeSafe Jev, together with the question instructions. Identifiers go in
`state.id` so you can audit which rows were judged. Quote location and numeric
modeling stay in this sidecar.

## Import and run

The directory contains a hyphen, so import it with `importlib`:

```python
from importlib import import_module

tf = import_module("text-features.kernel")

questions = tf.propose_questions(
    "Predict whether an abstract reports a significant clinical result.",
    examples,
    n=12,
)
table = tf.featurize(
    rows,
    questions,
    text_field="text",
    id_field="id",
)
study = tf.run_feature_study(
    rows,
    target="label",
    split_by="patient_id",  # or time_col="date"
    rounds=3,
    text_field="text",
    id_field="id",
)
```

`propose_questions` asks `host.llm` for Noul (yes/no facts) and Score
(written-level grades) questions, then validates and deduplicates them.

`featurize` calls `host.judge("features.custom", ...)` once per row. Each Noul
becomes one column, P(yes). Each Score becomes two columns: the expected level
normalized to [0, 1], and the standard deviation of that distribution on the
same scale. Unavailable rows are filled with NaN and counted; they are not
replaced with a default. Every column keeps the question text and template
version.

`run_feature_study` reuses `audit-dataset`, `plan-ml-experiment` (grouped or
chronological split), and `evaluate-model` (metrics and bootstrap 95% CI).
Question edits, feature screening, and thresholds use the development rows
only. The test split is judged once, after the question set is frozen. The
report includes lift versus a constant baseline and a bootstrap interval,
plus the question-set version and per-feature provenance.

Modeling uses numpy / pandas / scikit-learn when the science extra is
installed. They are imported lazily. They are not core dependencies. Without
them the sidecar still featurizes and falls back to a linear least-squares
fit.

## Required output

Name the split, the frozen question-set version, each feature's source
question and template version, unavailable-row counts, cost (requests and
tokens), the baseline, the lift, and the bootstrap interval. Never describe
the features as labels, facts, or a human gold standard.
