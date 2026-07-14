# Scientific kernel environments

[简体中文](README_zh.md)

These conda specifications define the four optional task environments created
by `openai4s setup`. They are execution-plane environments, not mandatory
dependencies of the standard-library control plane.

## Files

| File | Purpose |
|---|---|
| `python.yml` | General Python scientific environment used for data analysis and plotting workloads. |
| `phylo.yml` | Phylogenetics/bioinformatics-oriented environment and command-line tooling. |
| `r.yml` | R interpreter and packages required by the independent R kernel channel. |
| `struct.yml` | Structural biology/cheminformatics-oriented Python environment. |

The selected environment changes the worker interpreter, while routing,
permissions, storage, and Host RPC remain owned by the daemon. Keep optional
packages here rather than adding hard imports to the zero-dependency core.
