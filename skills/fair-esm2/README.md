# fair-esm2 Skill

Meta's ESM-2, driven through the external `fair-esm` package: per-residue and per-sequence embeddings, masked-language-model scores, mutation effects, and contacts. ESM-2 reads sequence and only sequence. It is never handed a structure and it does not inverse-fold — the contact map it returns is inferred from the residues alone, which is the opposite direction from the MPNN skills. One package trap sits in front of all of it: `fair-esm` and Biohub's ESM fork both import as `esm` and are different libraries. This recipe is the Meta one; the fork is covered by `esmfold2`.

No checkpoints are bundled here, and nothing in the recipe reserves the CPU or GPU capacity it asks for. The likelihoods and contacts a model hands back are computational predictions, and whether they hold for your task is something task-appropriate validation has to establish.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install fair-esm2 --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install fair-esm2`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/fair-esm2
python3 -m zipfile -c fair-esm2.zip fair-esm2
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/fair-esm2/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Picks a checkpoint (8M for a smoke test, 650M as the default, 3B when the embeddings have to be good), then walks through the alphabet and batch conversion, which representation layer to pull, pooled versus per-residue embeddings, mask-based mutation scoring, and contacts. Batching, memory, and what the model cannot tell you close it out. |
