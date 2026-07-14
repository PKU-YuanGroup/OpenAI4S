# Plan ML Experiment Skill

This progressive-disclosure Skill turns an ML question into a reproducible, leakage-aware experiment plan. Its pure-stdlib sidecar creates deterministic splits and manifests from caller-supplied metadata; it does not train a model or prove a split is scientifically appropriate.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Defines dataset/target/unit framing, random/grouped/chronological split choice, baselines, metrics, ablations, seeds, leakage checks, and minimum artifacts. |
| [`kernel.py`](kernel.py) | Optional sidecar: deterministic `random_split`, stable time-ordered `chronological_split`, leakage-safe `grouped_split`, canonical configuration fingerprints, file SHA-256, and JSON-compatible experiment manifests without invented environment state. |

## Direct subdirectories

None.

Group identifiers and chronology must come from domain knowledge; helper output cannot detect an incorrectly defined experimental unit.
