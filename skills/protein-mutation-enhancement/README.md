# Protein Mutation Enhancement Skill

An iterative protein mutation campaign: build a library, score it, rank it, decide whether to run another round. This is the orchestration layer, not a model. Library enumeration, score merging, ranking and loop control are deterministic and pure stdlib; the ESM, folding and assay numbers come from elsewhere — `fair-esm2` for sequence effect, `esmfold2` for structure — and are joined in on a stable variant ID like `A12V+G47D`. A variant ranking first is not evidence of gain of function.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install protein-mutation-enhancement --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install protein-mutation-enhancement`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/protein-mutation-enhancement
python3 -m zipfile -c protein-mutation-enhancement.zip protein-mutation-enhancement
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/protein-mutation-enhancement/` out. If you already
run OpenAI4S there is nothing to install — the wheel ships every bundled Skill,
and a bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The input contracts, how single, double and higher-order libraries are built, where the external scores come from (`fair-esm2` for sequence effect, `esmfold2` for structure), thresholded ranking, the rule for continuing a round or stopping, the practical defaults, and the validation the output still needs. |
| [`kernel.py`](kernel.py) | Optional sidecar, pure stdlib. Validates sequences and `A12V`-style mutation notation, normalizes and applies variants, and enumerates a library deterministically with stable position-sorted IDs, so a score table can safely join on `id`. It scores the substitution itself with a local heuristic over amino-acid class, hydropathy, charge and volume; reads score tables and writes the library out as FASTA; merges and normalizes the metrics; ranks by weighted composite score; runs a selection round against acceptance thresholds and reports whether to continue; suggests the positions worth opening next; and persists the ranked result as JSON. |

The built-in property score is a heuristic term in the composite, not a functional predictor. Whatever comes out on top still needs independent computational and experimental validation.
