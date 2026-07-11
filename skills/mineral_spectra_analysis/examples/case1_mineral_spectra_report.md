# Mineral Spectra Example: case1

- Source case: `examples/case1/spectrum.csv`
- Spectrum type: synthetic dirty mixed-mineral Raman spectrum
- Pipeline: global preprocessing once -> iterative residual peak-find/match/subtract -> NNLS unmixing -> final evaluation
- Ground truth is shown here only because this is an evaluation example; the blind loop does not read it.

## Synthetic Components

| Component | Type | True fraction | Role |
|---|---|---:|---|
| Clinoptilolite-Ca | mineral phase | 25.1% | hidden synthetic component |
| Bertrandite | mineral phase | 30.3% | hidden synthetic component |
| Diopside | mineral phase | 44.6% | hidden synthetic component |

## Blind Pipeline Prediction

| Predicted component | Type | Estimated fraction | Supporting peaks (cm^-1) |
|---|---|---:|---|
| Diopside | predicted mineral phase | 45.7% | 180.0, 232.0, 324.0, 356.0, 390.0, 530.0, 666.0, 1012.0 |
| Bertrandite | predicted mineral phase | 29.6% | 182.0, 202.0, 228.0, 356.0, 710.0, 820.0, 926.0, 986.0 |
| Clinoptilolite-Ca | predicted mineral phase | 24.7% | 220.0, 260.0, 482.0, 614.0 |

## Reliability Diagnostics

- Clean-spectrum second-derivative peaks: **28**
- First peak positions shown in source run: [180.0, 202.0, 226.0, 254.0, 298.0, 324.0, 358.0, 390.0, 484.0, 530.0, 558.0, 612.0, 666.0, 686.0, 710.0, 822.0, 926.0, 964.0, 986.0, 1012.0]
- Pearson fit correlation: **0.983**
- Residual RMSE: **0.0005**
- Relative residual: **0.1603**
- Explained energy: **97.3%**
- Remaining significant residual peaks: **0**
- Reliability: **HIGH**

## Ground-Truth Evaluation

- True components: ['Clinoptilolite-Ca', 'Bertrandite', 'Diopside']
- True fractions: {'Clinoptilolite-Ca': 0.25091200512204953, 'Bertrandite': 0.30268842086941916, 'Diopside': 0.44639957400853125}
- Precision/Recall/F1: 1.00 / 1.00 / **1.00**
- Fraction MAE: **0.007**

## Iteration Trace

| Step | Added component | Match corr | rel_residual | Residual peaks | Cumulative components |
|---|---|---:|---:|---:|---|
| 0 | Diopside | 0.843 | 0.5375 | 28 | ['Diopside'] |
| 1 | Bertrandite | 0.799 | 0.3049 | 31 | ['Diopside', 'Bertrandite'] |
| 2 | Clinoptilolite-Ca | 0.793 | 0.1603 | 32 | ['Diopside', 'Bertrandite', 'Clinoptilolite-Ca'] |

## Files

- `case1/spectrum.csv` - observable two-column Raman spectrum
- `case1/truth.json` - hidden answer key for this evaluation example
- `case1/input.png` - dirty input spectrum plot
- `case1_components.json` - component type and true-fraction summary
- `case1_analysis.json` - committed blind-analysis summary used by this report
