---
name: single-cell-rna-analysis
description: >
  Reproducible Scanpy workflow for human or mouse 10x scRNA-seq and snRNA-seq
  count matrices: preflight validation, sample-aware QC and Scrublet, optional
  explicitly requested Harmony, resolution-sweep clustering, evidence-assisted
  annotation, donor-aware pseudobulk DE and Milo DA, checkpoints, resume, and a
  checksummed analysis bundle. Use for cell-called GEX matrices, not FASTQ,
  CITE-seq, ATAC, Multiome, spatial, trajectory, communication, or CNV analysis.
origin: openai4s
category: workflow
---

# Single-cell RNA Analysis

Use this workflow for human or mouse 10x GEX scRNA-seq or snRNA-seq after cell
calling. It preserves raw counts, keeps descriptive cluster markers separate
from condition inference, and treats annotations as evidence until the user
confirms them.

## Before running

1. Read [the input contract](references/input-contract.md) and resolve every
   path plus the single requested contrast.
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
- Cells are not biological replicates. Inferential DE/DA requires at least
  three independent donors in each contrast level.
- Candidate labels, including reference transfer, are not ground truth.
- scVI, scGPT, GPU, remote compute, ambient correction, FASTQ processing and
  downstream specialty analyses require a separate, explicit workflow.
