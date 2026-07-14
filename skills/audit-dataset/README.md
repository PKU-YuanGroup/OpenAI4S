# Audit Dataset Skill

This progressive-disclosure Skill provides a pure-stdlib structural audit recipe for row-oriented tabular data. When loaded, its [`kernel.py`](kernel.py) sidecar adds reusable helpers to the persistent Python kernel; it does not read a dataset automatically or decide how domain-specific anomalies should be resolved.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Defines the pre-analysis workflow, interpretation rules, and required machine-readable output for schema drift, missingness, duplicates, target balance, and split leakage. |
| [`kernel.py`](kernel.py) | Optional sidecar centered on `audit_rows`: validates row/column arguments, creates deterministic representations for comparisons, summarizes missing/type/unique values, detects duplicate rows/IDs, counts targets, and reports entity overlap across splits without pandas/numpy. |

## Direct subdirectories

None.

A structural pass does not establish representativeness, label quality, or absence of near-duplicate leakage; those require domain review.
