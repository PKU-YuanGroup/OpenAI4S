# `workflows/evidence-package/`

**Exporting and verifying an evidence package** — The deliverable of a research run: a session package with a manifest, hashes and reproduction instructions, which verifies in a clean environment and refuses a single altered byte.

Steps: `open_session`, `save_artifact`, `export_session_package`
Permissions: `workspace:read`
Declared artifacts: `result.csv`, `session.zip`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `evidence-package/verifies` | `success` | The exported package passes the verifier |
| `evidence-package/tamper` | `provenance` | One altered byte and the verifier must refuse |

## Failure conditions the manifest declares

- the package fails its own verification
- the manifest does not cover every member
