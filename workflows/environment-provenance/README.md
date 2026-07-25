# `workflows/environment-provenance/`

**An artifact's environment provenance** — An artifact must record the **kernel generation that actually produced it**, never the daemon's own interpreter. A snapshot taken without a generation to measure has to say it is assumed — provenance that is wrong is worse than provenance that is missing, because it is believed.

Steps: `open_session`, `register_kernel_generation`, `capture_environment`
Permissions: `kernel:python`
Declared artifacts: —

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `environment-provenance/measured` | `provenance` | With a kernel generation, the snapshot is marked verified |
| `environment-provenance/assumed` | `provenance` | With no kernel generation, it must say so rather than assume |

## Failure conditions the manifest declares

- the snapshot records the daemon rather than the kernel
- generation attribution is unverifiable and not labelled as such
