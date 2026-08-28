# scGPT Skill

scGPT is a pretrained transformer over single-cell expression: cell embeddings, cell-type annotation, gene-level representations, all from an external checkpoint. Reach for it when you want a foundation model's view of a dataset rather than a generative model fitted to it — for the latter, `scvi-tools` trains scVI/scANVI on your own counts. Nothing scGPT needs in order to run is kept in this directory. No checkpoint, no vocabulary, no `AnnData`, no GPU environment.

The recipe cannot check your data for you. Confirm the checkpoint layout, that your gene names line up with the vocabulary, how the counts were preprocessed, and that batch metadata and labels are what you think they are. Zero-shot or fine-tuned, an annotation is a model output, not ground truth.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install scgpt --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install scgpt`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/scgpt
python3 -m zipfile -c scgpt.zip scgpt
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/scgpt/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | A scGPT checkpoint is a raw directory (`args.json`, `best_model.pt`, `vocab.json`), not a Hugging Face repo, so the loader takes a filesystem path and a hub id will not do. The gene names in `adata.var` have to line up with the checkpoint's vocabulary; the ones that do not are dropped without a word, so `gene_col` is worth checking before you read anything into the result. `embed_data` leaves the per-cell embedding in `.obsm["X_scGPT"]`, and annotation goes on from there. Then the batch and resource needs, the remote-compute path, and the two defaults that actually bite: `use_fast_transformer` is `True` and resolves to a FlashAttention path that may not import, and a stale torchtext `Vocab` shim shows up as a missing attribute rather than a clean failure. |
