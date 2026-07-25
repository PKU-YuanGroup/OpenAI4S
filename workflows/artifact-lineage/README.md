# `workflows/artifact-lineage/`

**A derived artifact carries its lineage** — When B is derived from A, the Store has to record that edge. Without it the evidence chain breaks at the second step, and an artifact that cannot name its inputs is a file rather than a result.

Steps: `open_session`, `save_raw`, `save_derived`, `assert_lineage`
Permissions: `workspace:write`
Declared artifacts: `raw.csv`, `derived.csv`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `artifact-lineage/derived` | `provenance` | A derived artifact records its input version |
| `artifact-lineage/missing-input` | `failure` | A declared input that does not exist must not fabricate an edge |

## Failure conditions the manifest declares

- the lineage edge is missing
- a derived artifact points at the wrong input version
