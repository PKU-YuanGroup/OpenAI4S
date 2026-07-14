# scvi-tools Skill

This progressive-disclosure recipe covers external scVI/scANVI workflows for count-based single-cell latent spaces, label transfer, and Bayesian differential expression. Loading the Skill can attach one small compatibility sidecar; scvi-tools, PyTorch, data, trained models, and GPU resources are not bundled.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Documents raw-count preservation, `setup_anndata`, scVI/scANVI training, embeddings/normalized expression, differential expression, outputs, remote-compute patterns, and common count/batch/device pitfalls. |
| [`kernel.py`](kernel.py) | Optional sidecar exposing `h5ad_safe_obs`, which copies an observation table and coerces its index and string-like columns into HDF5-safe representations before `.h5ad` serialization. |

## Direct subdirectories

None.

Convergence, batch correction, labels, and differential-expression conclusions must be checked on the actual dataset; recipe presence does not guarantee a compatible scvi-tools installation.
