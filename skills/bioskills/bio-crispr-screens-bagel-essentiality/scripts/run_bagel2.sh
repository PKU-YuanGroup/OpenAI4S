#!/bin/bash
# Reference: BAGEL2 2.0 (hart-lab/bagel, build 115) | Verify API if version differs
#
# BAGEL2 essentiality analysis end-to-end:
# 1. Compute per-sgRNA fold changes from counts
# 2. Compute per-gene Bayes Factors using CEGv2/NEGv1 reference sets
# 3. Precision-recall analysis to calibrate BF threshold

set -euo pipefail

# === INPUTS ===
COUNTS=counts.txt              # tab-separated: sgRNA, GENE, sample columns
CONTROL=Plasmid                # control sample column header
TREATMENT="Sample1,Sample2,Sample3"
CEG=CEGv2.txt                  # Hart 2017 core essentials
NEG=NEGv1.txt                  # Hart 2014 non-essentials
OUTDIR=bagel_results

mkdir -p "$OUTDIR"

# === DOWNLOAD REFERENCES IF MISSING ===
[ ! -f "$CEG" ] && curl -L -o "$CEG" \
    https://raw.githubusercontent.com/hart-lab/bagel/master/CEGv2.txt
[ ! -f "$NEG" ] && curl -L -o "$NEG" \
    https://raw.githubusercontent.com/hart-lab/bagel/master/NEGv1.txt

# === STEP 1: FOLD CHANGES ===
# bagel fc: per-sgRNA log-fold-change vs control
BAGEL.py fc \
    -i "$COUNTS" \
    -o "$OUTDIR/foldchange" \
    -c "$CONTROL" \
    --min-reads 30                          # default is 0; 30 is a common convention

# === STEP 2: BAYES FACTORS ===
# bagel bf: per-gene BF via summed log-likelihood ratios
# Default resampling is 10-fold cross-validation; -b -NB switches to bootstrapping
BAGEL.py bf \
    -i "$OUTDIR/foldchange.foldchange" \
    -o "$OUTDIR/bayes_factor.txt" \
    -e "$CEG" \
    -n "$NEG" \
    -c "$TREATMENT" \
    -b -NB 1000

# === STEP 3: PRECISION-RECALL CURVE ===
# Empirically calibrate BF threshold against CEGv2
BAGEL.py pr \
    -i "$OUTDIR/bayes_factor.txt" \
    -o "$OUTDIR/precision_recall.txt" \
    -e "$CEG" \
    -n "$NEG"

# === STEP 4: INTERPRETATION ===
# BF >=6 corresponds to ~90% posterior probability of essentiality (Hart 2017 G3,
# which pairs BF >=6 with FDR <=3% and BF >3 with FDR <5%); stricter BF cutoffs are
# BAGEL convention, not a published FDR mapping. Regenerate precision/recall per
# screen with BAGEL.py pr rather than assuming fixed values.
# BF <-6 indicates candidate tumor suppressor (negative selection)

echo "BAGEL2 analysis complete. Outputs in $OUTDIR/"
echo "  - foldchange.foldchange: per-sgRNA LFCs"
echo "  - bayes_factor.txt: per-gene BF + bootstrap CI"
echo "  - precision_recall.txt: PR curve at every BF threshold"
echo ""
echo "Recommended BF thresholds:"
echo "  Exploratory:        BF >0  (P=0.85, R=0.95)"
echo "  Standard:           BF >6  (~90% posterior; Hart 2017 pairs this with FDR <=3%)"
echo "  High-confidence:    BF >12 (stricter BAGEL convention)"
echo "  Ultra-stringent:    BF >30 (near-certain essentials; BAGEL convention)"
