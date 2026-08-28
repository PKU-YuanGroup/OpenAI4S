# Chai-1 Skill

The progressive-disclosure recipe for Chai-1, an all-atom diffusion co-folder that treats protein, RNA, DNA, and SMILES-ligand chains as first-class entities in one multi-entity FASTA. It walks the agent through the external `chai-lab` Python API and through the ranked candidates and scores that come back; the model itself is not shipped here. Chai-1 covers much the same ground as `boltz`, and the recipe leans on that: running both and keeping the designs that pass either is a common consensus filter, and Chai's Python entry point makes it the easier of the two to embed in a design loop.

The package, the weights, GPU resources, and optionally an external MSA service all have to be available separately. Running Chai-1 alongside Boltz-2 buys a second model, not a second experiment. Both are all-atom diffusion co-folders of the same family, so a complex they both like is a complex they can be wrong about together, and the ipTM they agree on is still the models talking about themselves. What survives both is a shorter list to take to the bench. That is all it is.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install chai1 --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install chai1`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/chai1
python3 -m zipfile -c chai1.zip chai1
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/chai1/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Most of it is about one choice: MSA-backed run or the ESM-embedding path. Skipping the MSA server is faster but typically lands a few ipTM points behind, and it saves no GPU memory, because the embedding path loads a traced 3-billion-parameter ESM2 next to the trunk. Around that decision sit the `>protein\|name=…` header syntax for the multi-entity FASTA, the `run_inference` arguments, the ranked `pred.model_idx_*.cif` files and their `scores.*.npz`, and why an unset `CHAI_DOWNLOADS_DIR` either re-pulls 5 GB on every cold start or dies mid-run with a `PermissionError`. Ends on running Chai-1 against a second model for consensus, plus licensing. |
