# ADMET Genetic Optimization Skill

Optimizing molecules from seed SMILES under ADMET guidance, with an auditable lineage from every candidate back to the seed it came from. The Python sidecar carries the reusable parts: SMILES normalization, the scoring contract, lineage validation, and visualization. It stops short of a fixed genetic algorithm on purpose, and it never validates candidate chemistry experimentally.

RDKit, pandas, matplotlib, ADMET-AI, PyTorch and the model assets are all optional, and have to be installed into a selected environment before any of this runs. The rest is the agent's job: read the data contracts, build the mutation, crossover and selection logic, keep the lineage intact, and treat every prediction as triage evidence rather than fact.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install admet_genetic --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install admet_genetic`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/admet_genetic
python3 -m zipfile -c admet_genetic.zip admet_genetic
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/admet_genetic/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The main recipe: prerequisites, seed normalization, the contracts that must be read first, how to assemble the GA, ADMET/SA/QED/property scoring, filters, diversity, lineage, outputs, reporting, and the limitations to state in the report. |
| [`kernel.py`](kernel.py) | The optional sidecar. It standardizes and canonicalizes SMILES, classifies ADMET-AI endpoints and aggregates them into a score plus risk flags, emits the canonical `operation_detail` JSON, and checks a generation log against the lineage contract. `render_optimization_history` turns that log into a self-contained dashboard, with RDKit molecule SVGs and matplotlib plots when those libraries are present. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | A committed, reproducible demonstration: inputs, recorded generations, the selections derived from them, a report and a dashboard. It is a fixture, not a live optimization result. |
| [`references/`](references/) | ADMET runtime notes, the data-contract and lineage rules, and GA design notes, read on demand through progressive disclosure. |
