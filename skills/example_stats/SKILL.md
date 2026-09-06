---
name: example_stats
description: descriptive-statistics helpers — summary (mean/std/median), quantile, zscore normalization, and Pearson correlation on plain Python number lists (no pandas/numpy).
origin: personal
capabilities:
  network:
    mode: none
    domains: []

---
# Skill: stats

Lightweight descriptive-statistics helpers. Use this when a task needs quick
summary statistics, quantiles, correlation, or z-score normalization on plain
Python number lists — no pandas/numpy required.

## Import

```python
from example_stats.kernel import summary, quantile, zscore, correlation
```

## Recipes

Describe a dataset in one call:

```python
data = [4, 8, 15, 16, 23, 42]
s = summary(data)
print(s) # {'n':6,'mean':18.0,'std':13.49...,'min':4,'max':42,'median':15.5}
```

Get an arbitrary quantile (0..1):

```python
p90 = quantile(data, 0.90)
print(p90)
```

Standardize values (z-scores, sample std):

```python
z = zscore(data)
print(z) # list of z-scores, mean~0 std~1
```

Pearson correlation between two equal-length series:

```python
r = correlation([1, 2, 3, 4], [2, 4, 6, 8])
print(r) # 1.0
```

## Notes

- `summary` uses SAMPLE std (n-1 denominator). Pass `population=True` for the
  n-denominator version.
- All functions raise `ValueError` on empty input.
- Everything is pure stdlib; safe to call inside the persistent kernel.
