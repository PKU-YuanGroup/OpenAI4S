# `workflows/r-analysis/`

**R is its own channel, not a wrapper** — R is an independent analysis channel rather than a Python wrapper. This workflow proves an R cell really executes in R.

Steps: `open_session`, `run_r_cell`
Permissions: `kernel:r`
Declared artifacts: —

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `r-analysis/happy` | `success` | The R cell returns R's own version string |
| `r-analysis/error` | `failure` | An R error must be reported as a failure |

## Failure conditions the manifest declares

- Rscript cannot be resolved
- the R cell errors
- the output came from Python rather than R
