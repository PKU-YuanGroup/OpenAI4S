# Input and configuration contract

## Supported boundary

The workflow accepts human or mouse, cell-called 10x GEX scRNA-seq/snRNA-seq
counts. It does not accept FASTQ, CITE-seq, scATAC-seq, Multiome or spatial
objects. One run is either a single-sample descriptive analysis or one
tested-versus-reference comparative analysis.

## Version 1 configuration

Required top-level fields are `schema_version: 1`, `organism` (`human` or
`mouse`), `modality` (`scrna` or `snrna`), `input`, `reference`, and
`integration`. `analysis_mode` is `descriptive` or `comparative`; omitting it
preserves the version-1 default of `comparative`. `design` is required only for
comparative mode. Relative paths are resolved against the configuration's
current working directory by `preflight()` and persisted as absolute paths in
`config.resolved.json`.

`reference` must name `gene_id_type`, `genome_build`, and
`annotation_release`. Accepted gene identifiers are `symbol`, `ensembl`, or
`ensembl_with_symbol`. When gene IDs do not expose symbols, provide a
`gene_symbol_column` in `reference` or mitochondrial/ribosomal metrics cannot
be established.

`input.mode` is one of:

- `sample_sheet`: `input.path` points to CSV with `sample_id`, `donor_id`,
  `condition`, `matrix_path`, and `matrix_format`. Formats are `10x_mtx`,
  `10x_h5`, or `h5ad`. Paths in the sheet resolve relative to the sheet.
- `h5ad`: `input.path` points to one object whose `obs` includes the configured
  sample, donor, condition, covariate, and batch columns.

For h5ad, `input.counts_layer` defaults to `counts`; set it to `X` only if `.X`
itself contains raw counts. Sample h5ad rows use the same rule. Nonnegative,
finite integer counts are mandatory; normalized-only input is rejected.

## Single-sample descriptive mode

Set `analysis_mode: descriptive` for one h5ad without a real experimental
contrast. This mode:

- requires `input.mode: h5ad` and a nonempty technical `input.sample_id`;
- accepts an h5ad whose `obs` has no sample, donor, or condition columns,
  and rejects one that still carries `donor_id` or `condition` columns
  (remove them or run a comparative analysis);
- writes `input.sample_id` into the configured `design.sample_key` only when
  that technical sample column is absent;
- trims surrounding whitespace from an existing technical sample column after
  confirming that its normalized values identify exactly one sample;
- rejects an h5ad containing more than one value in the sample column;
- requires `integration.method: none` and `statistics.de/da: false`; and
- rejects `design.tested` or `design.reference` instead of silently ignoring a
  copied contrast.

If `input.sample_id` is omitted, it defaults to the h5ad filename stem. This is
a technical object identifier, not inferred biological metadata. The workflow
never synthesizes donor, condition, treatment, batch, or replicate labels.

Sample and donor identifiers must be nonempty. Sample IDs must be unique in a
sample sheet; cell IDs become globally unique by prefixing `sample_id:`. All
samples must use the declared organism, genome build, annotation release and
gene namespace. If per-row reference columns are present, disagreement is a
hard failure.

## Design and integration gates

In comparative mode, `design` declares `tested`, `reference`, `condition_key`, `donor_key`, optional
`sample_key`, `covariates`, and `paired`. Both levels must occur. A paired
design requires donors represented in both levels. The workflow checks design
rank after cells have been loaded. Because model formulas and contrast strings
are built from these values, when DE or DA is enabled the `donor_key`,
`condition_key`, and covariate column names must be formula-safe identifiers
(letters, digits, underscores, not starting with a digit), and when DA is
enabled the `tested`/`reference` level values may contain only letters,
digits, underscores, and dots — Milo's contrast string cannot express other
characters. Rename the column or level, or disable the statistic.

`integration.method` is `none` or `harmony`. Harmony requires nonempty,
user-supplied `batch_keys`, and every key must be present. A requested batch
whose levels map one-to-one to condition is fully confounded and is rejected;
the workflow never guesses another key.

## Optional sections

- `qc`: MAD thresholds, optional study-specific hard thresholds, Scrublet
  enablement and upstream ambient-correction status.
- `clustering`: `resolutions` and `selected_resolution`.
- `annotation`: marker CSV, compatible reference h5ad and confirmed mapping
  CSV.
- `statistics`: enable/disable DE and DA separately.
- `seed`: fixed integer seed, default 0.
