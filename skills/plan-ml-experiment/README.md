# Plan ML Experiment Skill

Turning an ML question into a reproducible, leakage-aware plan, written down before training starts. One choice governs the rest: which unit has to stay independent — the patient, the scaffold, the site, the document, the point in time. Its pure-stdlib sidecar builds deterministic splits and manifests from metadata the caller supplies. It trains nothing, and it cannot tell you that a split is scientifically appropriate.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Naming the unit of analysis first, then reading the split off it: grouped for patients, scaffolds, sites, documents or repeated measures; chronological when deployment means predicting the future; random only when the rows genuinely are independent. Everything else hangs from that — hypothesis, intervention, baseline, primary metric and decision rule written before test performance is seen, then frozen seeds and configs, one-factor ablations, and the artifact set (fingerprint, checksums, split indices, per-example predictions) that lets someone rerun the comparison. Determinism is not validity: repeating one biased split reproduces the bias exactly. |
| [`kernel.py`](kernel.py) | Optional sidecar. `random_split` shuffles row indices under a seed, `chronological_split` orders them by timestamp without shuffling, and `grouped_split` keeps every group in exactly one partition. Alongside those: a canonical fingerprint for a configuration, SHA-256 for a file, and a JSON-compatible experiment manifest that records what it was given and invents no environment state. |

Group identifiers and chronology have to come from domain knowledge. If the experimental unit was defined wrong, nothing in the helper output will show it.
