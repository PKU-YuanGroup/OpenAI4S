# Example Stats Skill

This small progressive-disclosure Skill demonstrates the `SKILL.md` plus Python-sidecar pattern with dependency-free descriptive statistics. The sidecar is loaded only when the Skill is selected and operates on caller-supplied numeric sequences.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Gives import examples and recipes for summaries, quantiles, z-scores, and Pearson correlation. |
| [`kernel.py`](kernel.py) | Optional sidecar implementing input checks, `mean`, sample/population `std`, `median`, interpolated `quantile`, `zscore`, `correlation`, and a combined `summary` on plain Python number lists. |

## Direct subdirectories

None.

The helpers are educational/general-purpose calculations; they do not choose a statistical design or establish inferential validity.
