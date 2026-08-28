# scvi-tools Skill

scVI and scANVI, trained on your own data: a batch-corrected latent space over raw counts, semi-supervised label transfer on top of it, and Bayesian differential expression. Both models want raw integer UMI counts and nothing else will do, so much of the recipe is about not destroying them on the way in. Loading the Skill can attach one small compatibility sidecar, and only to the local analysis kernel. Everything else is yours to supply: scvi-tools itself, PyTorch, the data, the trained models, the GPU.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install scvi-tools --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install scvi-tools`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/scvi-tools
python3 -m zipfile -c scvi-tools.zip scvi-tools
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/scvi-tools/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Stash the raw counts in a layer before you normalize anything: feed log-normalized data to `setup_anndata` and scVI's negative-binomial likelihood produces silent garbage rather than an error. Then train scVI, put scANVI on top for label transfer, and read back the embeddings, the decoded expression, and the differential-expression table. It also pins down the two API changes that break older code — `use_gpu=` was removed in 1.x in favour of `accelerator="gpu", devices=1`, and `differential_expression` now defaults to `mode="vanilla"`, whose columns include no `lfc_*` and no `proba_de` at all — plus the remote-compute pattern and the `.h5ad` write that fails on Arrow-backed strings. |
| [`kernel.py`](kernel.py) | The optional sidecar. One function, `h5ad_safe_obs`: it copies an observation table and coerces the index and the string-like columns into HDF5-safe representations, so serializing to `.h5ad` afterwards does not fail on them. |

Convergence, batch correction, transferred labels, and differential-expression conclusions all have to be checked against the dataset in front of you. `pred_cell_type` is a classifier's guess rather than an annotation, and the differential-expression table describes the fitted generative model, not a measurement made on cells. And a recipe sitting on disk says nothing about whether a compatible scvi-tools is installed.
