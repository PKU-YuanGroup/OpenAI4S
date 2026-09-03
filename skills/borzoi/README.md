# Borzoi Skill

Borzoi predicts functional coverage tracks — RNA-seq, CAGE, DNase, ChIP — straight from DNA sequence. Reach for it to get predicted tracks across a locus, or to compare ref and alt windows around a variant when you want the assay-level consequence rather than a language model's likelihood; `evo2` is the skill for the likelihood, and the two answer different halves of the same variant question. What is here is guidance for driving an external PyTorch port. No model runtime and no checkpoint is bundled.

Whether any of it runs depends on the environment: compatible packages, weights already downloaded, the track metadata, and a substantial amount of GPU memory. A predicted track delta is model evidence you can prioritize on. It is not causal proof, and it is not clinical validation.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install borzoi --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install borzoi`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/borzoi
python3 -m zipfile -c borzoi.zip borzoi
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/borzoi/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The input window is fixed at 524,288 bp of one-hot DNA and the model exposes no attribute that says so, so a shape mismatch is the first thing you meet: pad or crop. Out comes a tensor of 7,611 human tracks over 32-bp bins, with the separate 2,608-track mouse head off unless you enable it and select it. From there: where the track metadata actually lives (`TRACKS_DF`, not a `targets` attribute the base model does not have) and how to line it up with the output, ref/alt variant scoring, the VRAM floor, and the CC-BY-4.0 terms on the ported weights, which do not match the Apache-2.0 code they came with. |
