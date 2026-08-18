# Perturb-Seq Analysis - Usage Guide

## Overview

Decision-grade analysis of single-cell pooled CRISPR screens. Covers Perturb-seq, CROP-seq, Perturb-CITE-seq, ECCITE-seq, Perturb-ATAC, and Multiome variants; MOI considerations; sgRNA assignment; escaper filtering with Mixscape (Papalexi 2021); calibrated low-MOI DE with SCEPTRE (Barry 2024); Pertpy unified framework; and genome-wide screens (Replogle 2022).

## Prerequisites

```bash
# Pertpy (Python; integrates Mixscape + SCEPTRE wrappers + DE methods)
pip install pertpy scanpy anndata muon

# SCEPTRE (R; for calibrated low-MOI DE)
R -e "devtools::install_github('katsevich-lab/sceptre')"   # sceptre is not on CRAN

# Seurat (R; for Mixscape native + multiome)
R -e "install.packages('Seurat')"
```

Required inputs:
- AnnData / Seurat object with scRNA-seq counts (cells x genes)
- Per-cell sgRNA assignment (from direct-capture or CROP-seq library)
- Sample / channel metadata (batch covariate)
- Non-targeting control sgRNA identifier

## Quick Start

Tell the AI agent what to do:
- "Analyze my CROP-seq experiment: sgRNA assignment, Mixscape escaper filter, SCEPTRE DE per perturbation, downstream pathway analysis"
- "Choose between direct-capture Perturb-seq and CROP-seq architecture for a planned 1000-pert screen in iPSC-derived neurons"
- "Scale up: design genome-wide Perturb-seq following the Replogle 2022 protocol (2.5M cells, ~9,866 expressed genes in K562, CRISPRi)"
- "Diagnose: why does my Mixscape filter out 80% of perturbed cells as escapers?"
- "Run SCEPTRE on my low-MOI Perturb-seq data and compare FDR calibration vs MAST"

## Example Prompts

### Architecture Selection

> "I'm running a 1,500-perturbation screen in primary T cells. Choose between Dixit Perturb-seq (direct capture) vs CROP-seq vs Perturb-CITE-seq. Required: scRNA + sgRNA detection from same library. Recommendation depends on cost vs sgRNA assignment rate."

> "Genome-wide essentiality screen in K562. Replicate Replogle 2022 design: 2,057 essential-gene perturbations via CRISPRi, 10X 3' direct capture, a median >100 cells/pert as screened (budget 500-1,000 if per-perturbation DE power is the goal). Estimate cost and channel count."

### sgRNA Assignment + Filtering

> "Assign sgRNAs per cell using threshold of 10 reads. Compute multiplet rate (cells with 2+ sgRNAs above threshold); flag for doublet filter."

> "Apply Mixscape to filter escapers. For each perturbation, compute perturbation signature = cell_expression - mean(K=20 nearest NTC cells). Classify KO vs NP cells. Keep only KO cells for DE."

### DE Analysis

> "Run SCEPTRE on KO-filtered cells. NB GLM with technical covariates (n_genes, n_umi, channel). Permutation FDR (1000 iterations). Output per-gene-per-pert log-fold-change + FDR."

> "Compare SCEPTRE vs MAST on the same Perturb-seq data. Show FDR calibration via permutation: expect MAST inflation; SCEPTRE calibrated."

### Genome-Wide Scale

> "Design genome-wide Perturb-seq for 19,000 protein-coding genes using one dual-sgRNA CRISPRi element per gene. Compute cells needed at 500/pert. Distribute across 10X channels."

> "For my 2-million-cell genome-scale dataset, run per-pert SCEPTRE; output a 2057-pert x 19000-gene matrix of log-FCs. Cluster perturbations by their gene-effect profiles to identify functional modules."

### Diagnostics

> "Mixscape filtered 78% of perturbed cells as escapers. Diagnose: weak phenotype (perturbation insufficient), wrong K parameter, or true escaper rate is high (Cas9 expression heterogeneous)?"

> "sgRNA assignment rate is 65% of cells (35% unassigned). Diagnose: under-loaded sgRNA library, wrong read threshold, or library-prep architecture mismatch?"

> "MAST DE called 8,000 significant genes per perturbation. Verify against SCEPTRE; SCEPTRE will likely call 500-1,500 -- the difference is MAST's uncalibrated FDR."

### Multi-Omic

> "I have 10X Multiome data with sgRNA capture (RNA + ATAC). Use muon for joint analysis; identify perturbation-specific chromatin + RNA changes."

> "Perturb-CITE-seq: integrate sgRNA + scRNA + surface ADT. Identify perturbations that change cell-surface phenotype."

## What the Agent Will Do

1. Identify experimental architecture (direct-capture vs CROP-seq vs Perturb-CITE vs Multiome)
2. Verify sgRNA library prep matches architecture
3. Standard scRNA QC: gene counts, UMI counts, mitochondrial %, doublet detection (Scrublet)
4. sgRNA assignment per cell (threshold ≥10 reads); compute assignment rate
5. Flag multiplets (cells with 2+ sgRNAs); decide to filter or analyze as combinatorial
6. Standard normalization (scanpy: total + log1p; or scran)
7. Apply Mixscape escaper filtering with NTC controls and K=20 nearest neighbors
8. Verify KO retention rate (strongly guide-dependent; Papalexi 2021 saw 39-92% across four guides against one gene)
9. Per-perturbation DE via SCEPTRE (low-MOI variant if applicable) with covariates (n_genes, n_umi, channel)
10. Permutation FDR with 1,000+ iterations
11. Aggregate per-perturbation signatures; pathway enrichment
12. For genome-scale: cluster perturbations by effect profiles
13. Output: per-pert DE tables, perturbation cluster heatmap, pathway analysis

## Tips

- The single most common silent failure: sgRNA library prep doesn't match scRNA architecture. CROP-seq sgRNA is in 3'UTR captured by 3' chemistry. Direct-capture Perturb-seq needs amplicon-PCR pre-sequencing. Mixing them fails silently with low assignment rate.
- For genome-wide screens, Replogle 2022 is the canonical protocol. Match: CRISPRi, 10X 3' chemistry, one dual-sgRNA element per gene, and a median >100 cells per perturbation (budget 500-1,000 per perturbation if per-perturbation DE power is the goal).
- Mixscape escaper filtering is mandatory for clean perturbation signal. A guide- and gene-dependent fraction of cells fail to edit (Papalexi 2021: ~25% escapers for IFNGR2, but a predicted 0% perturbation rate for 15 genes); including them dilutes effects. Skip Mixscape only if validating that Cas9 expression is uniform via independent assay.
- SCEPTRE is the only DE method with calibrated FDR on Perturb-seq scale (Barry 2024 benchmark). MAST and Wilcoxon over-call by 5-10x. Always use SCEPTRE.
- Cells per perturbation: 500 minimum for moderate effects; 1,000+ for genome-scale comparisons. Below 500, per-pert DE is unstable.
- MOI 0.3 is the standard for single-sgRNA-per-cell; at MOI 0.5, 9% of cells get multiple sgRNAs (not analyzable as single perturbation).
- For combinatorial Perturb-seq (intentionally high MOI), pair guide-pairs cassettes and analyze as combinatorial.
- Doublet rate from cell-loading (Scrublet / scDblFinder) is independent from sgRNA-multiplet rate; filter both.
- For multimodal screens (Perturb-CITE, Perturb-ATAC, Multiome), use muon (Python) or Seurat (R) for joint analysis.

## Architecture Cheat Sheet

| Use case | Architecture |
|----------|--------------|
| Standard scRNA + sgRNA, low cost | CROP-seq |
| Genome-wide CRISPRi (Replogle 2022) | Direct-capture + 10X 3' |
| Surface protein + sgRNA | Perturb-CITE-seq |
| Chromatin readout | Perturb-multiome (RNA+ATAC) |
| Hashed cells + sgRNA | ECCITE-seq |
| Cell barcoded + sgRNA | scAR-Trac (Tracr-RNA-barcoded) |

## Cell-per-Pert Targets

| Resolution | Cells per perturbation |
|------------|------------------------|
| Genome-scale | 500-1,000 |
| Focused (specific module) | 1,000-2,000 |
| Single-pert deep | 5,000+ |
| Combinatorial (pair) | 2,000+ per pair |

## Validation Checklist

- [ ] sgRNA assignment rate >70% of cells
- [ ] Multiplet rate <5% after doublet filtering
- [ ] Mixscape KO retention >40% per guide (flag any guide below; investigate near-0% rates)
- [ ] SCEPTRE permutation FDR calibrated (not MAST)
- [ ] Per-pert cell count ≥500
- [ ] Channel batch as SCEPTRE covariate
- [ ] NTC controls included (~5% of library)

## Related Skills

- crispr-screens/library-design - Direct-capture vs CROP-seq library design
- crispr-screens/screen-qc - sgRNA assignment as QC
- crispr-screens/mageck-analysis - Pseudobulk alternative
- crispr-screens/hit-calling - Pseudo-bulk hit calling
- single-cell/preprocessing - scRNA-seq preprocessing
- single-cell/clustering - Post-DE clustering of perturbations
- single-cell/multimodal-integration - Multiome Perturb-seq
- single-cell/perturb-seq - General single-cell screen tools
- pathway-analysis/go-enrichment - Pathway analysis
