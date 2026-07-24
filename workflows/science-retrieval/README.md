# `workflows/science-retrieval/`

**Scientific retrieval with source evidence** — A retrieved record has to answer two questions: when it was true, and whether these are the same bytes.

Steps: `science_query`, `connector_drift_check`
Permissions: `network:science`
Declared artifacts: —

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `science-retrieval/provenance` | `provenance` | The hash equals the upstream raw body |
| `science-retrieval/drift` | `success` | A required field set to null counts as drift |

## Failure conditions the manifest declares

- the hash is not of the upstream raw bytes
- the recorded length is not the length that arrived
