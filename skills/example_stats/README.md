# Example Stats Skill

A small progressive-disclosure Skill that demonstrates the `SKILL.md` plus Python-sidecar pattern with dependency-free descriptive statistics. The sidecar is loaded only when the Skill is selected, and it works on numeric sequences the caller passes in.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Import examples and short recipes for summaries, quantiles, z-scores, and Pearson correlation. |
| [`kernel.py`](kernel.py) | Optional sidecar over plain Python number lists: `mean`, sample or population `std`, `median`, an interpolated `quantile`, `zscore`, `correlation`, and a combined `summary`. Each one checks its input and refuses an empty sequence. |

These are ordinary calculations, meant for teaching and general use. They do not choose a statistical design, and they do not make an inference valid.
