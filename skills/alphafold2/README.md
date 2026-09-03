# AlphaFold2 Skill

AlphaFold2 and AlphaFold2-Multimer, run through ColabFold's `colabfold_batch` rather than DeepMind's own pipeline — which is what turns a prediction into one command over one FASTA instead of a 2 TB local database mount. The loader shows only the Skill name and summary up front; the agent reads [`SKILL.md`](SKILL.md) when a task actually calls for MSA-backed monomer or multimer folding, or for confidence and self-consistency review of a designed sequence. What separates AF2 from the three co-folders next to it is scope: it folds protein chains and nothing else, so ligands and nucleic acids get routed to `boltz`, `chai1`, or `openfold3`.

No AlphaFold code, weights, environment, or running service is bundled here. A prediction needs ColabFold, the model parameters, and compute of your own, and on the public MSA path it also sends the sequence out to the external ColabFold MMseqs2 service. The GPU requirement in the Skill metadata says what the recipe needs; it is not evidence that a GPU is there.

The scores the recipe ranks on measure the model's confidence in the coordinates it just drew, not their correctness. A high pLDDT says AF2 is sure of the fold it produced. An ipTM past the usual 0.5 soft pass says the Multimer model is sure of the interface it produced, and nothing at all about whether those two chains ever meet in a cell — AF2 will fold a heterodimer that does not exist, and it will look confident doing it. Treat a passing rank-1 model as a hypothesis to test, not as a result.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install alphafold2 --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install alphafold2`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/alphafold2
python3 -m zipfile -c alphafold2.zip alphafold2
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/alphafold2/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | What the agent reads once it has decided to fold: the `colabfold_batch` invocation, the fact that a colon between chains in the FASTA is what actually switches AF2 into Multimer mode, and how to rank the five output models on pLDDT and ipTM before trusting any of them. Two runtime traps get sections of their own — the unified-memory defaults ColabFold sets on import, which make the first fold hang forever without erroring in a confined GPU sandbox, and the public MMseqs2 server, which is shared and rate-limited and eats most of the wall clock on a short job. Sequences going out to that server, and the CC-BY-4.0 terms on the AF2 weights, are stated up front. |
