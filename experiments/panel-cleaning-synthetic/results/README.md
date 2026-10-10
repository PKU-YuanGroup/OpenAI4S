# Synthetic experiment outputs

[中文说明](README_zh.md)

Both branches use the actual Skill with one fixed screen. evaluation.json keeps imputation sample counts, shared-mask comparisons, all numeric candidates and unconditional/eligible-event recovery. verification.json may later record independent checks and is excluded from the run manifest.

## Files

| Entry | Purpose |
| --- | --- |
| `artifact_manifest.json` | Recorded runtime/source/output checksums. |
| `environment.json` | Recorded runtime/source/output checksums. |
| `evaluation.json` | Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report. |
| `fit_profile.json` | Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report. |
| `imputation_errors.csv` | Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report. |
| `method_decisions.json` | Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report. |
| `overview.png` | Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report. |
| `overview.svg` | Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report. |
| `filled-outcome/` | Independent preserved Skill output bundle. |
| `main/` | Independent preserved Skill output bundle. |

Optional independent verification: `verification.json`.
