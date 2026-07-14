# Scientific kernel environments

[中文说明](README_zh.md)

The four conda specs that `openai4s setup` turns into optional task
environments. They live in the execution plane; the standard-library control
plane runs without any of them.

## Files

| File | Purpose |
| --- | --- |
| `python.yml` | Python 3.11 for general data analysis and plotting. Carries the single-cell stack (scanpy, anndata, leidenalg, umap-learn), scikit-learn and the usual numerics, and pulls rdkit and fair-esm from pip. |
| `phylo.yml` | Python 3.11 for phylogenetics and bioinformatics. Alongside biopython, dendropy and ete3 it installs the command-line tools a tree pipeline needs: mafft, iqtree, fasttree, trimal. |
| `r.yml` | R 4.5.3 and the packages the independent R kernel channel expects: tidyverse, data.table, ggplot2, knitr/rmarkdown, jsonlite. conda-forge only, no bioconda. |
| `struct.yml` | Python 3.13 for structural biology and protein language models. biotite and biotraj come from conda; torch and fair-esm come from pip, deliberately as the portable CPU build. Substitute a conda pytorch build for a GPU-accelerated one. |

Selecting an environment changes which interpreter the worker runs under.
Routing, permissions, storage and Host RPC stay with the daemon. New optional
packages belong in these files rather than as hard imports in the
zero-dependency core.
