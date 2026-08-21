# BAGEL2 Essentiality - Usage Guide

## Overview

Decision-grade essentiality calling for CRISPR-Cas9 fitness screens using BAGEL2 (Kim & Hart 2021 *Genome Medicine*). Computes Bayes Factors per gene by log-likelihood ratios over per-sgRNA fold changes, anchored against CEGv2 reference core-essentials (684 genes) and NEGv1 reference non-essentials (927 genes). Improvements over BAGEL1: linear extrapolation enables tumor-suppressor detection; multi-target off-target correction reduces false positives. BF >=6 corresponds to ~90% posterior probability of essentiality (Hart 2017); stricter BF cutoffs are BAGEL convention rather than a published FDR mapping.

## Prerequisites

```bash
# BAGEL2 (Python)
git clone https://github.com/hart-lab/bagel
# Run BAGEL.py directly from the clone; the repo has no setup.py and no PyPI release

# Reference gene sets
wget https://raw.githubusercontent.com/hart-lab/bagel/master/CEGv2.txt
wget https://raw.githubusercontent.com/hart-lab/bagel/master/NEGv1.txt
```

Required inputs:
- Count matrix (tab-separated, columns: sgRNA, GENE, then sample columns)
- Reference essential genes (CEGv2.txt)
- Reference non-essential genes (NEGv1.txt)
- Control sample (Day 0 or plasmid) for fold-change baseline

## Quick Start

Tell the AI agent what to do:
- "Run BAGEL2 on my Brunello screen: compute fold changes then Bayes factors using CEGv2/NEGv1; pick BF threshold from precision-recall analysis"
- "Identify tumor-suppressor candidates from BAGEL2 negative BF values; cross-check against COSMIC tumor suppressor list"
- "Compare BAGEL2 BF vs MAGeCK FDR on the same dataset; reconcile disagreements at BF 5-7 boundary"
- "Diagnose BAGEL2 calling no hits despite known essentials -- is it a reference-set issue?"
- "Calibrate BF threshold for clinical-grade essentiality: BF >12 vs BF >30, using BAGEL.py pr on my own screen"

## Example Prompts

### Standard Workflow

> "Run BAGEL.py fc then bf on counts.txt with controls Plasmid and treatment samples Drug_r1,Drug_r2,Drug_r3. Output bayes_factor.txt sorted by BF descending."

> "Run BAGEL.py pr after to generate precision-recall curve against CEGv2; pick the BF threshold that reaches 95% precision on my screen's own PR curve."

### Tumor Suppressor Detection

> "From BAGEL2 output, identify genes with BF <-6 (significantly negative). Cross-reference against COSMIC tumor suppressor gene list. Output candidate tumor suppressors with their BF, fold change, and sgRNAs."

> "BAGEL2 calls 80 tumor-suppressor candidates in my dropout screen. Many don't replicate in literature -- investigate whether my screen design supports tumor-suppressor calling (drug-modifier vs simple dropout)."

### Calibration and Comparison

> "Compare BAGEL2 BF >6 hits vs MAGeCK RRA neg|fdr <0.05 hits on the same screen. Compute Jaccard similarity. Investigate top 10 disagreements."

> "Adjust BF threshold based on screen quality. High-quality screen (PR-AUC against CEGv2 >0.85) supports BF >6. Lower-quality may need BF >12 for same precision."

### Diagnostics

> "My BAGEL2 returns no genes with BF >0. Diagnose: wrong reference set file, library coverage too low, or genuinely flat screen?"

> "Per-sgRNA LLR contributions show one guide dominating BF for several hits. Apply second-best-sgRNA rule from [[hit-calling]] to filter these guide-of-one hits."

## What the Agent Will Do

1. Verify input file format: sgRNA, GENE, sample columns
2. Download/verify CEGv2 and NEGv1 reference files from hart-lab
3. Run `BAGEL.py fc` to compute fold changes from control samples
4. Run `BAGEL.py bf` to compute Bayes Factors (default: 10-fold cross-validation; `-b -NB 1000` to bootstrap)
5. Run `BAGEL.py pr` to generate precision-recall curve
6. Apply BF threshold (default BF >6, ~90% posterior per Hart 2017)
7. Stratify genes: essential (BF >6), neutral (-6 to 6), candidate tumor suppressors (BF <-6)
8. For tumor suppressors, cross-check against COSMIC / published tumor suppressor lists
9. Compare to MAGeCK output (if also run); report consensus hits
10. Cross-check per-sgRNA LLR contributions for low-efficacy guides
11. Output essential genes ranked by BF, tumor suppressor candidates flagged, library calibration metrics

## Tips

- The single most common silent failure: wrong reference gene set file (truncated, gene-symbol mismatch). Always verify by counting genes in CEGv2.txt and NEGv1.txt before running.
- BF >6 corresponds to ~90% posterior probability against CEGv2 in Hart 2017 (the ~5% FDR mapping is a rough BAGEL convention). For cell types outside cancer (iPSC, primary T cells), this calibration may not hold; use a cell-type-specific essentialome derived for the relevant lineage.
- BAGEL2's tumor-suppressor sensitivity comes from linear extrapolation in the BF calculation. This is a real improvement over BAGEL1 and worth using for drug-modifier screens or screens expecting positive selection.
- For copy-number-confounded cancer-line screens, pre-correct with CRISPRcleanR (see [[copy-number-correction]]) before BAGEL2; otherwise, amplified regions will appear as "essential" with high BF.
- The resampling CI (STD column) is the diagnostic for guide-quality issues. Wide CI = low confidence; investigate per-sgRNA contributions.
- For screens with only 3 sgRNAs/gene (some custom libraries), switch to bootstrapping with `-b -NB` to 5000+ for stable estimates.
- When BAGEL2 and MAGeCK disagree, BAGEL2 typically calls more hits in screens with high background variance (it's robust due to reference-set anchoring) and fewer in screens with strong NTC null distribution.
- For non-cancer cell types, cross-check that CEGv2 genes drop out at expected rate. If not, use cell-type-specific reference.

## Decision Cheat Sheet

| Question | Answer |
|----------|--------|
| Standard cancer-line essentiality | BAGEL2 with CEGv2/NEGv1; BF >6 threshold |
| Drug-modifier screen wanting both directions | BAGEL2 (tumor-suppressor sensitive) or drugZ |
| Multi-cell-line panel | Chronos (DepMap) preferred over BAGEL2 |
| iPSC / primary cell / non-cancer | Use cell-type-specific essentialome |
| Custom library, <4 sgRNAs/gene | Bootstrap with `-b -NB`; consider MAGeCK alternative |
| Clinical-grade essentiality | BF >12 or higher |

## Thresholds

Precision and recall are screen-specific -- generate them with `BAGEL.py pr` rather than assuming fixed values. The BF ladder used in practice:

| BF threshold | Use |
|--------------|-----|
| 0 | Exploratory; highest recall |
| 6 | Standard call (~90% posterior, Hart 2017) |
| 12 | High-confidence |
| 30 | Ultra-stringent; near-certain essentials |

## Validation Checklist

- [ ] CEGv2 and NEGv1 files downloaded and verified
- [ ] Resampling left at the 10-fold cross-validation default, or `-b -NB 1000` used deliberately
- [ ] PR curve generated against CEGv2 for empirical threshold selection
- [ ] Per-sgRNA LLR distributions checked for outliers
- [ ] Hits cross-validated with MAGeCK or JACKS (consensus tier)
- [ ] Tumor-suppressor calls (if any) cross-checked against COSMIC TS list

## Related Skills

- crispr-screens/mageck-analysis - MAGeCK as alternative
- crispr-screens/drugz-chemogenomic - drugZ for drug screens (also tumor-suppressor sensitive)
- crispr-screens/jacks-analysis - JACKS efficacy diagnostics for guide-of-one
- crispr-screens/hit-calling - Cross-method consensus
- crispr-screens/screen-qc - Pre-BAGEL CEGv2 PR-AUC
- crispr-screens/library-design - 4-6 sgRNAs/gene library standard
- crispr-screens/copy-number-correction - Pre-correction for cancer-line screens
- pathway-analysis/go-enrichment - Downstream
