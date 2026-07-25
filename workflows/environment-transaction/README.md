# `workflows/environment-transaction/`

**plan -> apply -> rollback as a transaction** — An environment change is a transaction: a failed apply must leave the current environment exactly as it was, and a rollback is a pointer move to a generation that is still on disk.

Steps: `environment_transaction`
Permissions: `environment:apply`
Declared artifacts: —

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `environment-transaction/rollback` | `recovered` | Roll back to the first generation after two |
| `environment-transaction/failed-apply` | `success` | A failed build leaves `current` untouched |

## Failure conditions the manifest declares

- a failed build moved the current pointer
- a rollback needs a rebuild
