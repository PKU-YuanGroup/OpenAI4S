# `workflows/remote-compute/`

**submit -> poll -> harvest against a real shell** — Send the heavy work out and bring the results and their evidence back. The remote script executes in a real shell.

Steps: `open_session`, `remote_job`
Permissions: `compute:ssh`
Declared artifacts: `scores.csv`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `remote-compute/harvest` | `success` | The declared output really comes back |
| `remote-compute/unwritten-output` | `success` | Promised but not produced must count as a failure |

## Failure conditions the manifest declares

- a declared artifact was never harvested
- a cancelled job reports success
- the exit code is lost
