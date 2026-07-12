---
name: mineral_spectra_analysis
description: Raman mineral mixture spectra analysis pipeline for unknown mixed-mineral spectra; preprocess noisy spectra once, iteratively match residual peaks against a reference spectral library, unmix components with NNLS, diagnose reliability, write reports, and optionally generate/evaluate synthetic benchmark cases with hidden ground truth.
origin: openai4s
category: spectroscopy
---
# Skill: mineral spectra analysis

Use this skill when a task asks for mixed mineral Raman spectrum component
prediction, spectral-library matching, Raman mixture unmixing, or a blind
diagnostic report explaining which minerals are present, approximate fractions,
supporting peaks, residual quality, and confidence.

This skill packages the `spectra-pipeline/` workflow. Preserve the pipeline
order exactly:

1. load a two-column spectrum (`raman_shift,intensity`)
2. align it to the reference library grid
3. apply global preprocessing exactly once: despike, denoise, baseline
   correction, normalize
4. save `clean_spectrum.csv`
5. read `clean_spectrum.csv` back into the loop
6. repeat on the residual: second-derivative peak detection -> peak-driven
   library match -> NNLS refit of all selected components -> subtract
7. stop when residual peaks vanish, correlation is too low, or residual gain is
   too small
8. report components, fractions, supporting peaks, residual diagnostics, and
   confidence
9. only if an answer key is explicitly supplied, evaluate against ground truth
   after the blind loop is complete

Do not read `truth.json` during analysis. Synthetic case generation and
ground-truth evaluation are optional utilities, not part of the main inference
path.

## Dependencies

The OpenAI4S core remains stdlib-only, but this skill's runtime analysis needs:

```bash
python -m pip install numpy scipy pybaselines matplotlib
```

To build the default RRUFF library from the internet, allow network access or
provide an existing local `excellent_oriented.zip` cache. The original prototype
uses RRUFF `excellent_oriented`.

Default spectral database:

```text
https://www.rruff.net/zipped_data_files/raman/excellent_oriented.zip
```

When `build_rruff_library(dataset="excellent_oriented", cache_dir="spectra_cache")`
downloads the database, it stores the ZIP as:

```text
spectra_cache/excellent_oriented.zip
```

If `cache_dir` is omitted, the skill uses `./spectra_cache/excellent_oriented.zip`
relative to the current working directory. You can avoid downloading by passing
`zip_path="/path/to/excellent_oriented.zip"` explicitly.

## Import

```python
from mineral_spectra_analysis.kernel import (
    analyze_spectrum_file,
    build_rruff_library,
    default_config,
    evaluate_against_truth,
    generate_synthetic_cases,
    load_spectrum_csv,
    load_truth,
    run_blind_loop,
)
```

## Main Workflow

For a user's own spectrum and spectral library, do not generate synthetic data.
Load or build the library, then run the blind pipeline:

```python
lib = build_rruff_library(
    dataset="excellent_oriented",
    max_minerals=120,
    cache_dir="spectra_cache",          # or pass zip_path="/path/to/excellent_oriented.zip"
)

cfg = default_config()
cfg["top_k"] = 8

result = analyze_spectrum_file(
    spectrum_csv_path="sample_spectrum.csv",
    library=lib,
    output_dir="outputs/run_sample",
    config=cfg,
    make_figures=True,
)

print(result["report_path"])
print(result["outcome"].best_result.fractions)
print(result["outcome"].best_result.diagnostics)
```

Outputs:

```text
outputs/run_sample/
├── clean_spectrum.csv      # one-time preprocessed spectrum, read back by loop
├── iterations.jsonl        # residual loop trace
├── report.md               # conclusion, confidence, support peaks, config
└── figures/                # optional diagnostic plots
```

The report should state:

- identified components and estimated fractions
- supporting Raman peaks for each component
- number of clean-spectrum peaks
- Pearson fit correlation, residual RMSE, relative residual, explained energy
- residual peak positions and reliability (`high`, `moderate`, or `low`)
- the fixed config used for preprocessing, matching, and NNLS
- the iteration trace: added component, match correlation, residual, cumulative
  component set

## Reference Library

Use `build_rruff_library(...)` for the RRUFF ZIP format. It mirrors the original
pipeline:

- tolerant parser for RRUFF `.txt` files because some files contain non-data
  lines
- one representative spectrum per mineral
- prefer `Processed` files and wider grid coverage
- resample every spectrum to the common 150-1400 cm^-1 grid at 2 cm^-1 spacing
- clip negative intensity and area-normalize each reference column

For a custom library, construct a `Library(grid, names, A)` object where `A` is
shape `(n_grid, n_minerals)` and each column is an area-normalized reference
spectrum on the same grid.

## Optional Evaluation

Only create synthetic cases or read answer keys when the user asks to evaluate
pipeline reasonableness, benchmark a case, or reproduce the prototype.

Generate blind cases:

```python
lib = build_rruff_library(zip_path="spectra_cache/excellent_oriented.zip", max_minerals=120)
generate_synthetic_cases(lib, out_dir="cases", n=5, seed=0, noise=0.02)
```

Each case contains:

```text
caseN/
├── spectrum.csv   # observable input; safe for the blind loop
├── truth.json     # hidden answer key; evaluate only after the loop
└── input.png      # optional dirty-spectrum visualization
```

Evaluate after the blind loop:

```python
analysis = analyze_spectrum_file(
    "cases/case1/spectrum.csv",
    lib,
    output_dir="outputs/run_case1",
    truth_path="cases/case1/truth.json",
)
print(analysis["evaluation"])
```

The analysis loop must not receive the truth dict. `evaluate_against_truth(...)`
computes precision/recall/F1 and fraction MAE only after the loop returns.

## Example Case

This skill includes a bundled case1 example copied from the prototype
`spectra-pipeline/cases/case1` directory:

```text
skills/mineral_spectra_analysis/examples/case1/
├── spectrum.csv   # observable synthetic dirty Raman spectrum
├── truth.json     # hidden answer key, used only for example evaluation
└── input.png      # dirty-spectrum plot
```

The example's component summary is:

```text
skills/mineral_spectra_analysis/examples/case1_components.json
```

It records that each mixture element is a mineral phase component from the RRUFF
reference library:

- Clinoptilolite-Ca — mineral phase, true fraction 0.2509
- Bertrandite — mineral phase, true fraction 0.3027
- Diopside — mineral phase, true fraction 0.4464

The rendered example report is:

```text
skills/mineral_spectra_analysis/examples/case1_mineral_spectra_report.md
```

It is regenerated from committed source data rather than hand-edited:

```bash
uv run python skills/mineral_spectra_analysis/examples/build_example.py
```

`case1_analysis.json` holds the committed blind-analysis summary and
`case1_components.json` holds the synthetic component types and hidden
fractions. Re-running the full numerical analysis from `case1/spectrum.csv`
requires the scientific runtime dependencies and an RRUFF library cache; this
small builder only regenerates the example report from committed data.

## Analyst Checklist

Before submitting a conclusion:

- verify the input spectrum was aligned to the library grid
- confirm preprocessing ran once and `clean_spectrum.csv` was written
- inspect the loop history for weak late components or tiny residual gains
- confirm the final reconstruction overlays the clean spectrum reasonably
- treat `moderate` or `low` reliability as a warning that the library may miss a
  component, preprocessing may be poor, or the sample may contain phases outside
  the database
- do not claim experimental certainty; identify the spectral evidence and the
  limits of the library-driven fit
