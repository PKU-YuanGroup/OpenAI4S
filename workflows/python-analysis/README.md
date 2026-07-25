# `workflows/python-analysis/`

**Python analysis producing a traceable artifact** — Run analysis code in the persistent Python kernel and register the result as a checksummed artifact. This is the most ordinary research run the system performs.

Steps: `open_session`, `run_python_cell`, `save_artifact`
Permissions: `kernel:python`, `workspace:write`
Declared artifacts: `scores.csv`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `python-analysis/happy` | `success` | Compute and write a CSV |
| `python-analysis/cell-error` | `failure` | Analysis code that raises must fail, not produce silently |

## Failure conditions the manifest declares

- the cell raises
- a declared artifact was never written
- the artifact checksum is missing
