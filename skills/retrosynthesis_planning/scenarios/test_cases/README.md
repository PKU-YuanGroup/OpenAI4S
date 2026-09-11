# Scenario test cases

[中文](README_zh.md)

This directory owns evaluation examples, dataset provenance, installation, and
private scoring. It is deliberately separate from `../pipelines/`. The bundled
JSON cases are CC0 synthetic protocol checks; they verify schemas, isolation,
budgets, hashes, and evaluator wiring but make no model-accuracy claim.

Install one case in a single command:

```bash
uv run python skills/retrosynthesis_planning/scenarios/test_cases/install.py \
  --case skills/retrosynthesis_planning/scenarios/test_cases/01_single_step_retrosynthesis.json \
  --workspace /tmp/openai4s-retro-case
```

The installer creates disjoint `public/`, `private_evaluator/`, and `results/`
directories plus an `installation.json` containing every installed file hash.
The GT pipeline checks the public hashes without opening private files. The
evaluator verifies both boundaries before scoring; changed or missing files
invalidate the installation instead of silently changing a frozen score.
Run the matching public pipeline, freeze its output, and only then evaluate:

```bash
uv run python skills/retrosynthesis_planning/scenarios/gt_codebases/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
uv run python skills/retrosynthesis_planning/scenarios/test_cases/evaluate.py \
  --scenario single_step --workspace /tmp/openai4s-retro-case
```

Each case's `private_evaluator/references.json` deliberately does **not** repeat
the model's own rank-1 public output: a reference copied from the prediction
makes every accuracy 1.0 by construction, and a scorer returning a constant
would pass. `tests/test_retrosynthesis_evaluator.py` pins the resulting
non-trivial values, so "correcting" a reference back to the model's answer is a
red test, not a silent improvement.

`database_sources.json` is the production-data registry. Entries marked
`not_frozen` must not be silently downloaded or republished. A maintainer must
first freeze the source revision, license decision, split, and SHA256; until
then, only the bundled protocol fixture is installable.

## Production source verification (2026-09-09)

This read-only audit checked official documentation and small metadata responses,
not dataset bytes. All six registry entries still had `release_status: not_frozen`
and no `revision`, `license`, `split`, or `sha256`. The candidates below are
preparation for maintainer review, not approved or frozen benchmark data. A code
commit identifies the documentation or preprocessing implementation; it does not
pin an external Dropbox, Box, or Google Drive archive.

| Scenario | Candidate source and identity | License evidence and remaining admission work |
| --- | --- | --- |
| `single_step` | [GLN](https://github.com/Hanjun-Dai/GLN/tree/b5bd7b181a61a8289cc1d1a33825b2c417bed0ef), commit `b5bd7b181a61a8289cc1d1a33825b2c417bed0ef`, links the Schneider50K raw CSV directory and the RetroSim train/validation/test split. | GLN code is MIT. Review the selected derived data archive separately; identify its actual files and freeze the documented 1,000-target selection, precursor references, split manifest, and SHA256. |
| `multistep` | [PaRoutes, Zenodo record 6275421](https://zenodo.org/records/6275421), DOI `10.5281/zenodo.6275421`, version `1.0.0`; the six n1/n5 files are listed below. | The [record API](https://zenodo.org/api/records/6275421) explicitly declares dataset license `cc-by-4.0`; the repository's Apache-2.0 license covers code. A maintainer must record admission and attribution, then freeze file SHA256, public/private target partitions, stock normalization, and budget configuration. |
| `atom_mapping` | [RXNMapper](https://github.com/rxn4chemistry/rxnmapper/tree/a01ecdcd5ac944850e9691739c1df858e005fd39), commit `a01ecdcd5ac944850e9691739c1df858e005fd39`, points to [RXNMapperData](https://ibm.box.com/v/RXNMapperData). No exact truth file has been selected. | MIT code is not evidence of a particular data file's license or independent truth. Select the file, license basis, and responsible truth reviewer; establish at least 1,000 unique scoreable reactions, symmetry-aware correspondences, bond changes, ambiguity decisions, and grouped splits. Predictions from the tested mapper cannot supply its own truth. |
| `forward` | [MolecularTransformer](https://github.com/pschwllr/MolecularTransformer/tree/aeb339daf0a029b391f8307fb3f467f461605dd2), commit `aeb339daf0a029b391f8307fb3f467f461605dd2`, links [USPTO/data.zip](https://github.com/wengong-jin/nips17-rexgen/blob/fb7dea369b0721b88cd0133a7d66348d244f65d3/USPTO/data.zip) and the [tokenized data package](https://ibm.box.com/v/MolecularTransformerData). The upstream examples use `src-test.txt` and `tgt-test.txt`. | MIT is a code-license observation, not a decision about the derived USPTO split. Verify the selected separated package and exact members, retain its test partition, and record role conversion, deduplication, private references, and SHA256. Do not assume an upstream `test.csv`; a CSV produced locally must identify its source files and transformation. |
| `conditions` | [Parrot download script](https://github.com/wangxr0526/Parrot/blob/0fb2325567e21011589641544e32427c8244e2a9/preprocess_script/download_data.py), commit `0fb2325567e21011589641544e32427c8244e2a9`, names `USPTO_condition_final.zip`, Google Drive file ID `1aX70qzZrJ9TZ9KpqnvUVR8WBxiTwXOsI`. | The repository declares MIT for code; an independent license basis for this external data archive remains unconfirmed. Retain original split/index, condition vocabulary and full tuple references, then hash them. The separately admitted HF model snapshot does not admit this dataset; Reaxys remains excluded. |
| `yield` | [rxn_yields](https://github.com/rxn4chemistry/rxn_yields/tree/d9e6b87ce1b881978490d68bfc00021e3b48127a), commit `d9e6b87ce1b881978490d68bfc00021e3b48127a`, lists `data/Buchwald-Hartwig/Dreher_and_Doyle_input_data.xlsx` (2,134,762 bytes). Its [training script](https://github.com/rxn4chemistry/rxn_yields/blob/d9e6b87ce1b881978490d68bfc00021e3b48127a/training_scripts/launch_buchwald_hartwig_training.py) names `FullCV_01`–`FullCV_10` and `Test1`–`Test4` worksheets. | Review the original HTE table and derived split permissions separately from MIT code or model weights. Verify actual worksheet contents and split boundaries; preserve random IID and all four MFF OOD evaluations, units, input columns, references, and SHA256. |

The [Lowe original USPTO metadata](https://api.figshare.com/v2/articles/5104873)
declares **CC0**, version `1`, DOI `10.6084/m9.figshare.5104873.v1`. This is useful
upstream evidence, but the relationship and terms of each selected derivative
still need review. `USPTO_MIT` identifies a dataset/split convention; the MIT
code license is not a license conclusion for that derived data.
The [HF metadata at the admitted model revision](https://huggingface.co/api/models/xiaoruiwang/ChemEnzyRetroPlanner_metadata/revision/b9ef6049d341bfc62d835f09ad6ce33b6f86b047)
declares MIT; that observation does not establish permission for Parrot's
separate Google Drive training/test data.

### PaRoutes file evidence

These are published metadata, not locally verified bytes. The six required
benchmark files total **125,966,045 bytes**; the rest of the 2.7 GB record need
not be fetched just to acquire these targets, stocks, and references.

| File | Published bytes | Published MD5 (not SHA256) |
| --- | ---: | --- |
| `n1-targets.txt` | 465689 | `5adae99357cdad829073b197c7813152` |
| `n1-stock.txt` | 394013 | `b15d92af317d5639ae69a422c7a1f1c1` |
| `n1-routes.json` | 52012998 | `219c2466795cd7aad6a86d6a797eadc5` |
| `n5-targets.txt` | 495657 | `c05ad490f2f5e203631d05a45d7e9e7f` |
| `n5-stock.txt` | 392504 | `eb2cf057cd8be772f2ba4652d48c6bec` |
| `n5-routes.json` | 72205184 | `a6e814e53fcfa358e6f0e5bceb14d711` |

### Decisions and verification still required

A maintainer must record the license basis and allowed use/redistribution for
each selected dataset, including attribution obligations. If that basis is
unresolved, distribute only acquisition instructions and evidence, and keep the
dataset `not_frozen`. Atom mapping additionally needs a concrete independent
truth source and reviewer; a mapper checkpoint or download landing page is
insufficient. These are the remaining human decisions, not fields an agent may
fill from repository license badges.

After approved acquisition, compute SHA256 from the actual input and generated
benchmark files, build deterministic split and deduplication manifests, and
verify private-reference isolation. Preserve upstream MD5 as supplementary
integrity evidence; neither MD5 nor a Git commit/blob SHA1 substitutes for
SHA256. Checkpoint training-overlap evidence is a separate prerequisite for
formal scientific scores. The existing
`test_production_database_registry_fails_closed_until_frozen` checks that the
four fields are nonempty when a row is marked `frozen`; passing it alone proves
neither licensing review nor file-hash or truth validation.

## Files

| File | Purpose |
| --- | --- |
| `install.py` | Boundary-preserving one-command test-data installer. |
| `evaluate.py` | Private-side evaluator entrypoint. |
| `database_sources.json` | Production dataset requirements and release state. |
| `01_single_step_retrosynthesis.json` | Scenario 1 synthetic evaluation case. |
| `02_multistep_route_planning.json` | Scenario 2 synthetic evaluation case. |
| `03_atom_mapping.json` | Scenario 3 synthetic evaluation case. |
| `04_forward_prediction.json` | Scenario 4 synthetic evaluation case. |
| `05_condition_recommendation.json` | Scenario 5 synthetic evaluation case. |
| `06_yield_estimation.json` | Scenario 6 synthetic evaluation case. |
| `README_zh.md` | Chinese directory guide. |
