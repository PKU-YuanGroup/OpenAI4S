# Scientific kernel environments

[中文说明](README_zh.md)

The four conda specs that `openai4s setup` turns into optional task
environments. They live in the execution plane; the standard-library control
plane runs without any of them.

## Files

| File | Purpose |
| --- | --- |
| `python.yml` | The default kernel env: Python 3.11 for general data analysis and plotting. Carries the single-cell stack (scanpy, anndata, leidenalg, umap-learn), scikit-learn and the usual numerics. rdkit comes from conda-forge and deliberately not from the pip `rdkit-pypi` wheel, which is frozen at 2022.9.5, built against the NumPy 1.x C ABI, and segfaults the kernel worker against the NumPy 2.x this spec resolves. fair-esm, the pinned scanpy[harmony,skmisc] and pydeseq2, and pypdfium2 come from pip; pertpy (Milo DA) deliberately stays out — its core drags the Flax/NumPyro/ott-jax stack — and is installed via the optional `singlecell` uv extra instead. |
| `phylo.yml` | Python 3.11 for phylogenetics and bioinformatics. Alongside biopython, dendropy and ete3 it installs the command-line tools a tree pipeline needs: mafft, iqtree, fasttree, trimal. |
| `r.yml` | R 4.5.3 and the packages the independent R kernel channel expects: tidyverse, data.table, ggplot2, knitr/rmarkdown, jsonlite. conda-forge only, no bioconda. |
| `struct.yml` | Python 3.13 for structural biology and protein language models. biotite, biotraj, Biopython, FreeSASA, and DSSP come from conda, covering PDB/mmCIF handling, solvent-accessible surface area, and secondary-structure assignment; torch and fair-esm come from pip as the portable CPU build. Substitute a conda pytorch build for a GPU-accelerated one. RFdiffusion is deliberately excluded: its upstream Python 3.9 and CUDA-specific older PyTorch/DGL stack belongs in a pinned dedicated environment or container, not this shared environment. |

Selecting an environment changes which interpreter the worker runs under.
Routing, permissions, storage and Host RPC stay with the daemon. New optional
packages belong in these files rather than as hard imports in the
zero-dependency core.
