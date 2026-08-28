# Evo 2 Skill

Evo 2 is a long-context DNA language model, and this is the operating guidance for it: likelihood scoring, embeddings, generation, and variant comparison. It answers questions about the sequence itself — how likely is this base, this window, this edit. When the question is instead what an assay would have measured, that is `borzoi`, and a two-axis variant prioritization runs both. The model code, the checkpoints, and the accelerator runtime are all external.

GPU capacity, usable context length, checkpoint access, and generation quality depend on the environment you actually run in, so confirm them there instead of assuming them from this page. And a likelihood is a model score: it is not a measured variant effect.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install evo2 --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install evo2`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/evo2
python3 -m zipfile -c evo2.zip evo2
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/evo2/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Sequences go in as plain `list[str]`; hand `score_sequences` a tensor and it dies on a dtype mismatch, because the API tokenizes for you. It gives back one mean log-likelihood per sequence, and a variant is scored as `Δll = ll_alt - ll_ref` over a fixed window. `generate` returns `.sequences`, `.logits` and `.logprobs_mean`, all populated with no flag to set. Embedding genomic windows is in the declared scope too. Then the model table — 7B in roughly 22 GB, 40B in roughly 78 GB, both at a million nucleotides of context — the remote-compute path for those checkpoints, and the failure modes worth recognizing early, including an `HF_HOME` on a read-only mount that only breaks when the loader tries to write `refs/`. |
