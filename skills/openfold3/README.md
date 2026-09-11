# OpenFold3 Skill

The progressive-disclosure recipe for OpenFold3, the AlQuraishi Lab's open-weights PyTorch reproduction of AlphaFold3. It covers how to set the model up and how to run it, and it carries none of it: no OpenFold3 code, no databases, no parameters, no prepared environment. Two things set it apart from the sibling co-fold recipes. OpenFold3 does not read FASTA at all, so a query is a JSON object validated against a strict schema. And its MSA server flag defaults to true, which means the sequence leaves the machine unless the run explicitly opts out.

Which protein, nucleic-acid, ligand, template, and accelerator paths actually work depends on the upstream version installed and on the assets present. Read the aggregated confidence file for what it is. `sample_ranking_score` sorts the samples of one run against each other, so the best sample of a query that never had an answer still comes back ranked first. `has_clash: 0.0` says the atoms do not overlap, which is a fact about the geometry the model drew. Those numbers are the model marking its own work. Whether the complex exists is a question they cannot reach.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install openfold3 --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. For a recipe included in the npm release,
`npx @pku-yuangroup/openai4s-skills install openfold3 --target claude` installs
the published copy. The npm catalog can differ from this repository.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/openfold3
python3 -m zipfile -c openfold3.zip openfold3
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/openfold3/` out (`unzip main.zip 'OpenAI4S-main/skills/openfold3/*'`
unpacks only that directory). If you already run OpenAI4S there is nothing to
install — the wheel ships every bundled Skill, and a bundled Skill takes
precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Leads with the query JSON, because getting its shape wrong is the first way a run fails: a `queries` dict, one `molecule_type` per chain, `sequence` for protein/DNA/RNA and `smiles` or `ccd_codes` for a ligand, all validated by a pydantic model that rejects unknown keys outright. Next the two flags that are on unless you turn them off — `--use-msa-server` POSTs the sequence to `api.colabfold.com`, and `--use-templates` additionally needs `data.rcsb.org` reachable — so an offline or egress-restricted run has to pass `false` to both. Then the gated Hugging Face weight download, the DeepSpeed attention kernel and the cuEquivariance fallback for when DeepSpeed is missing, the aggregated confidence file and what its numbers should look like, a troubleshooting table for the import and OOM failures, and upstream licensing. |
