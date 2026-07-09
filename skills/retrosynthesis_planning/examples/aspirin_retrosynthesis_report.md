# Retrosynthesis Planning Report

Target SMILES: `CC(=O)Oc1ccccc1C(=O)O`

## Executive Summary

- Candidate routes analyzed: 5
- Routes reaching stock/purchasable materials: 5
- Recommendation: prioritize solved, high-score, short routes and review reaction feasibility manually.

## Ranked Routes

### Route 1

- Solved: True
- Score: 0.998
- Estimated steps: 1
- Starting materials: `CC(=O)OC(C)=O`, `O=C(O)c1ccccc1O`
- Retrosynthetic rationale: The route reaches stock/purchasable terminal precursors; prioritize it by score 0.998, estimated 1 step(s), and terminal precursor set (CC(=O)OC(C)=O, O=C(O)c1ccccc1O). Treat this as a planning hypothesis until reaction conditions and literature precedent are checked.

### Route 2

- Solved: True
- Score: 0.998
- Estimated steps: 1
- Starting materials: `CC(=O)Cl`, `O=C(O)c1ccccc1O`
- Retrosynthetic rationale: The route reaches stock/purchasable terminal precursors; prioritize it by score 0.998, estimated 1 step(s), and terminal precursor set (CC(=O)Cl, O=C(O)c1ccccc1O). Treat this as a planning hypothesis until reaction conditions and literature precedent are checked.

### Route 3

- Solved: True
- Score: 0.998
- Estimated steps: 1
- Starting materials: `CC(=O)O`, `O=C(O)c1ccccc1O`
- Retrosynthetic rationale: The route reaches stock/purchasable terminal precursors; prioritize it by score 0.998, estimated 1 step(s), and terminal precursor set (CC(=O)O, O=C(O)c1ccccc1O). Treat this as a planning hypothesis until reaction conditions and literature precedent are checked.

### Route 4

- Solved: True
- Score: 0.998
- Estimated steps: 1
- Starting materials: `CC(=O)O`, `O=C(O)c1ccccc1F`
- Retrosynthetic rationale: The route reaches stock/purchasable terminal precursors; prioritize it by score 0.998, estimated 1 step(s), and terminal precursor set (CC(=O)O, O=C(O)c1ccccc1F). Treat this as a planning hypothesis until reaction conditions and literature precedent are checked.

### Route 5

- Solved: True
- Score: 0.994
- Estimated steps: 2
- Starting materials: `CC(=O)OC(C)=O`, `O=C(OCc1ccccc1)c1ccccc1O`
- Retrosynthetic rationale: The route reaches stock/purchasable terminal precursors; prioritize it by score 0.994, estimated 2 step(s), and terminal precursor set (CC(=O)OC(C)=O, O=C(OCc1ccccc1)c1ccccc1O). Treat this as a planning hypothesis until reaction conditions and literature precedent are checked.

## Molecule Briefs

### `CC(=O)Oc1ccccc1C(=O)O`

- Role: target
- Appears in routes: 1, 2, 3, 4, 5
- Stock status: not a terminal precursor
- Interpretation: Target molecule being disconnected into simpler purchasable or stock precursors.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=CC%28%3DO%29Oc1ccccc1C%28%3DO%29O

### `CC(=O)Oc1ccccc1C(=O)OCc1ccccc1`

- Role: intermediate
- Appears in routes: 5
- Stock status: not a terminal precursor
- Interpretation: Predicted synthetic intermediate; inspect functional groups, stereochemistry, and whether downstream disconnections are plausible.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=CC%28%3DO%29Oc1ccccc1C%28%3DO%29OCc1ccccc1

### `CC(=O)Cl`

- Role: stock precursor
- Appears in routes: 2
- Stock status: in stock
- Interpretation: Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=CC%28%3DO%29Cl

### `CC(=O)O`

- Role: stock precursor
- Appears in routes: 3, 4
- Stock status: in stock
- Interpretation: Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=CC%28%3DO%29O

### `CC(=O)OC(C)=O`

- Role: stock precursor
- Appears in routes: 1, 5
- Stock status: in stock
- Interpretation: Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=CC%28%3DO%29OC%28C%29%3DO

### `O=C(O)c1ccccc1F`

- Role: stock precursor
- Appears in routes: 4
- Stock status: in stock
- Interpretation: Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=O%3DC%28O%29c1ccccc1F

### `O=C(O)c1ccccc1O`

- Role: stock precursor
- Appears in routes: 1, 2, 3
- Stock status: in stock
- Interpretation: Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=O%3DC%28O%29c1ccccc1O

### `O=C(OCc1ccccc1)c1ccccc1O`

- Role: stock precursor
- Appears in routes: 5
- Stock status: in stock
- Interpretation: Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints.
- Suggested query: https://pubchem.ncbi.nlm.nih.gov/#query=O%3DC%28OCc1ccccc1%29c1ccccc1O

## Review Notes

- Confirm reagent availability, price, purity, and vendor lead time.
- Check stereochemistry, protecting-group logic, chemoselectivity, and hazardous transformations.
- Treat predicted reaction trees as planning hypotheses, not experimental validation.
- Record backend version, model files, stock file, and search parameters for reproducibility.
