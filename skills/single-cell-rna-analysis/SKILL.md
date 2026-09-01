---
name: single-cell-rna-analysis
description: >
  Reproducible Scanpy workflow for human or mouse 10x scRNA-seq and snRNA-seq
  count matrices: single-sample descriptive QC, clustering and annotation, or
  comparative donor-aware pseudobulk DE and Milo DA; plus preflight validation,
  optional explicitly requested Harmony, checkpoints, resume, and a checksummed
  analysis bundle. Use for cell-called GEX matrices, not FASTQ, CITE-seq, ATAC,
  Multiome, spatial, trajectory, communication, or CNV analysis.
origin: openai4s
category: workflow
capabilities:
  network:
    mode: none
    domains: []

---

# Single-cell RNA Analysis

Use this workflow for human or mouse 10x GEX scRNA-seq or snRNA-seq after cell
calling. It preserves raw counts, keeps descriptive cluster markers separate
from condition inference, and treats annotations as evidence until the user
confirms them.

## Before running

1. Read [the input contract](references/input-contract.md), select exactly one
   `analysis_mode`, and resolve every input path. Use `descriptive` only for a
   single h5ad without a valid condition contrast; otherwise use `comparative`.
2. Run `preflight(config)`. Do not proceed when `status` is `invalid`.
3. Show the user warnings about ambient RNA, confounding, annotation evidence,
   or insufficient donor replication before interpreting results.
4. Harmony is opt-in only. Never infer a batch key or silently replace a
   confounded one.

## Call the workflow

The directory contains hyphens, so import it with `importlib`:

```python
import importlib

single_cell = importlib.import_module("single-cell-rna-analysis.kernel")
config = {
    "schema_version": 1,
    "analysis_mode": "comparative",
    "organism": "human",
    "modality": "scrna",
    "input": {"mode": "sample_sheet", "path": "samples.csv"},
    "reference": {
        "gene_id_type": "symbol",
        "genome_build": "GRCh38",
        "annotation_release": "GENCODE 46",
    },
    "design": {
        "tested": "stim",
        "reference": "control",
        "condition_key": "condition",
        "donor_key": "donor_id",
        "paired": True,
        "covariates": [],
    },
    "integration": {"method": "none", "batch_keys": []},
}

check = single_cell.preflight(config)
result = single_cell.run(config, "single-cell-run")
```

For a single h5ad with no donor or condition metadata, use descriptive mode:

```python
config = {
    "schema_version": 1,
    "analysis_mode": "descriptive",
    "organism": "human",
    "modality": "scrna",
    "input": {
        "mode": "h5ad",
        "path": "pbmc3k.h5ad",
        "counts_layer": "X",
        "sample_id": "pbmc3k",
    },
    "reference": {
        "gene_id_type": "symbol",
        "genome_build": "hg19",
        "annotation_release": "GENCODE 19",
    },
    "integration": {"method": "none", "batch_keys": []},
}
```

Descriptive mode never invents donor/condition labels, performs integration,
or emits inferential DE/DA. It runs raw-count validation, within-sample QC,
embedding, resolution-sweep clustering, descriptive markers and optional
evidence-assisted annotation.

`run()` and `resume()` return `status`, `run_dir`, `featured_files`, `warnings`,
`annotation_status`, `statistics_status`, and `manifest`. Save every featured
file as an Artifact:

```python
for featured_file in result["featured_files"]:
    host.save_artifact(featured_file)
```

If a run was interrupted, call:

```python
resumed = single_cell.resume("single-cell-run")
```

Resume validates the resolved configuration and input hashes. A changed source
invalidates dependent checkpoints instead of mixing results from different
inputs.

## Stage routing

- Input ambiguity or validation failure: use
  [the input contract](references/input-contract.md).
- QC, Scrublet, representation, Harmony, clustering, and marker questions: use
  [the scientific workflow](references/scientific-workflow.md).
- Marker panels, reference evidence, `Unknown`, or confirmed labels: use
  [the annotation contract](references/annotation-contract.md).
- Pseudobulk DE, pairing, donor replication, or Milo DA: use
  [the statistics contract](references/statistics-contract.md).
- Checkpoints, statuses, manifest, or Artifact delivery: use
  [the output contract](references/output-contract.md).

## Non-negotiable interpretation rules

- A normalized-only matrix is not valid input for formal analysis.
- Multiple samples do not imply that integration is appropriate.
- UMAP appearance does not establish an optimal clustering resolution.
- Cluster markers are descriptive and are not condition DE.
- Descriptive mode cannot support condition, donor, treatment or causal claims.
- Cells are not biological replicates. Inferential DE/DA requires at least
  three independent donors in each contrast level.
- Candidate labels, including reference transfer, are not ground truth.
- scVI, scGPT, GPU, remote compute, ambient correction, FASTQ processing and
  downstream specialty analyses require a separate, explicit workflow.
