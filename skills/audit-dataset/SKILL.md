---
name: audit-dataset
description: Audit tabular datasets before analysis or training for schema drift, missing values, duplicate rows or IDs, target imbalance, and entity or group leakage across splits using pure-stdlib helpers.
origin: openai4s
category: data-quality
---

# Audit a dataset

Use this skill before statistics, model training, or external publication. The
goal is a compact, machine-readable audit plus explicit decisions about every
issue that could invalidate downstream results.

## Workflow

1. Load records without silently coercing values. Preserve source row IDs.
2. Call `audit_rows` on a representative or complete list of row mappings.
3. Inspect missingness and observed type mixtures column by column.
4. Resolve duplicate records and non-unique identifiers deliberately.
5. If a split column exists, check both stable IDs and grouping entities for
   train/validation/test overlap.
6. Record accepted exceptions, then rerun the audit and save the JSON result
   next to the cleaned dataset.

## Import and run

Hyphenated Skill directories are loaded with `importlib`:

```python
from importlib import import_module

audit_rows = import_module("audit-dataset.kernel").audit_rows
report = audit_rows(
    rows,
    target="label",
    id_columns=("sample_id",),
    group_columns=("patient_id",),
    split_column="split",
)
```

`rows` must be a sequence of mappings. The report contains row and column
counts, per-column missing/type/unique summaries, duplicate row and ID counts,
target frequencies, and split-leakage examples.

## Interpretation

- Mixed numeric/string types usually indicate parsing or sentinel-value bugs.
- Missingness is a property of both the data and the collection process; do
  not impute before checking whether it correlates with label, site, or time.
- Duplicate IDs are not automatically duplicate observations. Decide whether
  repeated measures are expected and group them during splitting.
- Any patient, molecule scaffold, time series, or near-duplicate entity shared
  across evaluation boundaries can inflate performance even when row IDs differ.
- A clean structural audit does not establish representativeness, label
  validity, causal identifiability, or ethical suitability.

## Required output

Report the checks performed, blocking findings, accepted exceptions, and the
exact source artifact/version. Never describe a dataset as clean without naming
the leakage keys and missing-value policy that were checked.
