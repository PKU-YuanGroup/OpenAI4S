# `workflows/permission-boundary/`

**The workspace boundary refuses a write outside it** — The agent chooses the path it writes to, so the boundary has to be code rather than convention.

Steps: `open_session`, `host_file_write`
Permissions: `workspace:write`
Declared artifacts: —

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `permission-boundary/inside` | `success` | An ordinary write inside the workspace |
| `permission-boundary/escape` | `permission_denied` | A write outside it must be refused |

## Failure conditions the manifest declares

- an out-of-boundary path is accepted
- a symlink walks out of the boundary
