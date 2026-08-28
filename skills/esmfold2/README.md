# ESMFold2 Skill

The progressive-disclosure recipe for Biohub's ESMFold2 and ESMFold2-Fast co-folding models, and for the ESMC protein language models from the same release. Nothing is vendored here: no code, no weights, no Hugging Face access, no GPU environment.

Which models, backends and versions actually exist has to be checked in the active environment. The paper's own FoldBench figure for antibody-antigen is a 50–55% DockQ pass rate, so about half of those interfaces are wrong, and the protocol behind numbers like that is 25 seeds by 5 diffusion samples with the best of the 125 taken as the answer — one fold ranked by its own ipTM is a thinner result than the headline suggests. The PDB training cutoff is September 2021; anything solved since, the model has never seen. Structures, mutation scores, contacts and interpretability features are computed predictions, and none of them should be presented as experimentally validated.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install esmfold2 --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install esmfold2`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/esmfold2
python3 -m zipfile -c esmfold2.zip esmfold2
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/esmfold2/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The whole recipe: how to describe protein, DNA, RNA and ligand inputs, when single-sequence mode is enough and when to feed an MSA, and how many diffusion steps and trunk recycles it takes to reproduce the paper's numbers. It also explains what the fused kernel backend actually buys you: roughly a 1.5–6x speedup on the trunk, growing with sequence length, though short folds are diffusion-bound and only break even around L≈300–400. (The gotchas list quotes a flat ~12x against the paper's numbers; the detailed section is the one to trust.) Fused and reference structures agree to within noise. After that, how to read the confidence outputs, how to pick a model variant, and where the upstream weights and their license come from. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Read on demand: notes on the experimental design hook, and the ESMC language-model recipes. |
