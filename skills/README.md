# Bundled Skills

[简体中文](README_zh.md)

This tree contains the 32 bundled OpenAI4S Skills. A Skill is a progressively
disclosed recipe of code and operational guidance, not a provider JSON Tool.
The loader exposes name and summary first, then loads `SKILL.md` and an
optional `kernel.py` sidecar only when selected.

## Subdirectories

| Directory | Responsibility |
|---|---|
| [`admet_genetic/`](admet_genetic/) | Builds an ADMET-guided genetic molecule-optimization workflow from seed SMILES, with explicit lineage, filtering, scoring, dashboards, and candidate triage. |
| [`alphafold2/`](alphafold2/) | Runs monomer or multimer structure prediction through the ColabFold AlphaFold2 runner and documents MSA, GPU, confidence, and self-consistency constraints. |
| [`audit-dataset/`](audit-dataset/) | Audits tabular data for schema drift, missingness, duplicates, imbalance, and entity leakage with deterministic standard-library helpers. |
| [`boltz/`](boltz/) | Guides Boltz-2 prediction for protein, nucleic-acid, and ligand complexes, including optional affinity output and operational failure modes. |
| [`borzoi/`](borzoi/) | Uses Borzoi to predict functional genomic tracks from DNA and compare track deltas for regulatory or non-coding variant analysis. |
| [`catalyst_sar_screening/`](catalyst_sar_screening/) | Provides a hard-locked FAIRChem UMA workflow for single-atom-catalyst SAR screening and requires every user result to come from a fresh pipeline run. |
| [`chai1/`](chai1/) | Runs Chai-1 co-folding for protein, nucleic-acid, and small-molecule complexes with explicit download, MSA, and GPU guidance. |
| [`diffdock/`](diffdock/) | Performs blind diffusion docking of small molecules into protein structures and ranks pose geometry by model confidence; it does not claim affinity. |
| [`esmfold2/`](esmfold2/) | Covers Biohub ESMFold2 all-atom folding plus ESMC language-model embeddings, mutation scoring, contacts, and backend-performance constraints. |
| [`evaluate-model/`](evaluate-model/) | Evaluates binary classification and regression with held-out metrics, tie-aware AUC, deterministic bootstrap uncertainty, baselines, and subgroup checks. |
| [`evo2/`](evo2/) | Scores, embeds, and generates long-context DNA with Evo 2 for variant, regulatory, and coding-sequence workflows. |
| [`example_stats/`](example_stats/) | Demonstrates a small user-style Skill whose sidecar supplies summary statistics, quantiles, z-scores, and correlation without NumPy or pandas. |
| [`fair-esm2/`](fair-esm2/) | Uses Meta ESM-2 for protein embeddings, masked-language-model mutation effects, and contact prediction. |
| [`figure-composer/`](figure-composer/) | Turns a scientific claim into a multi-panel figure plan, delegates panel work, composes outputs, and drives bounded adversarial review. |
| [`figure-style/`](figure-style/) | Defines correctness, legibility, layout, palette, labeling, and render-then-verify rules plus reusable plotting helpers. |
| [`indication-dossier/`](indication-dossier/) | Builds a resumable therapeutic-indication dossier across population, epidemiology, biology, standard of care, regulation, trials, and synthesis. |
| [`ligandmpnn/`](ligandmpnn/) | Inverse-folds structures while preserving ligand, nucleic-acid, or metal context for pocket and coordination-site design. |
| [`literature-review/`](literature-review/) | Retrieves, verifies, expands, and synthesizes scientific literature while checking DOI identity, retractions, evidence strength, and citation grounding. |
| [`mineral_spectra_analysis/`](mineral_spectra_analysis/) | Preprocesses mixed-mineral Raman spectra, iteratively matches residual peaks, performs NNLS unmixing, and reports reliability without reading hidden truth. |
| [`openfold3/`](openfold3/) | Guides OpenFold3 all-atom structure prediction, input JSON, weights, MSA behavior, outputs, and verification. |
| [`paper-narrative/`](paper-narrative/) | Reviews the story told by a manuscript and figure deck, identifies arc and missing evidence, and hands per-figure claims to the composer. |
| [`pdf-explore/`](pdf-explore/) | Parses a PDF once into persistent page text and provides outline, search, extraction, figure-crop, and parallel page-analysis helpers. |
| [`plan-ml-experiment/`](plan-ml-experiment/) | Plans leakage-safe, reproducible ML experiments with deterministic splits, fingerprints, checksums, seeds, baselines, ablations, and manifests. |
| [`protein-mutation-enhancement/`](protein-mutation-enhancement/) | Builds deterministic mutant libraries, merges sequence/structure/property scores, ranks candidates, and controls iterative gain-of-function rounds. |
| [`proteinmpnn/`](proteinmpnn/) | Inverse-folds a protein backbone into sequence, with chain constraints, fixed positions, checkpoint selection, and temperature sweeps. |
| [`remote-compute-nvidia/`](remote-compute-nvidia/) | Defines the NVIDIA NIM BYOC workflow for hosted or self-hosted modes, including provider policy, job submission, harvesting, and secret boundaries. |
| [`remote-compute-ssh/`](remote-compute-ssh/) | Defines approval-aware submit, notification, harvest, recovery, and host-learning patterns for SSH or SLURM compute. |
| [`retrosynthesis_planning/`](retrosynthesis_planning/) | Normalizes and ranks AiZynthFinder-style routes, queries molecules, renders route dashboards, and writes evidence-calibrated synthesis reports. |
| [`scgpt/`](scgpt/) | Uses scGPT for single-cell embeddings, cell-type annotation, and gene representations for perturbation or regulatory analysis. |
| [`scvi-tools/`](scvi-tools/) | Uses scVI/scANVI for probabilistic single-cell integration, latent spaces, label transfer, and Bayesian differential expression. |
| [`solublempnn/`](solublempnn/) | Inverse-folds backbones with a solubility-biased ProteinMPNN model while documenting the limits of sequence-only solubility claims. |
| [`using-model-endpoint/`](using-model-endpoint/) | Documents a planned endpoint-scoped inference workflow. The current Host implements endpoint registration and probes, but does not wire this provider into `ComputeManager` or create the scoped kernel yet. |

## Framework relationship

- `openai4s/skills_loader/` discovers these directories and applies bundled-name
  precedence over writable user Skills.
- Bundled Skills are read-only application resources. User-authored versions
  live under the configured data directory and cannot replace a bundled name.
- A `kernel.py` sidecar is definition-only, compile-checked, and injected into
  the scientific Python kernel; it must not add a hard dependency to the core.
- Provider shims are trusted extension code and run across separately documented
  compute or endpoint boundaries. Their manifests do not make a capability
  operational by themselves.
