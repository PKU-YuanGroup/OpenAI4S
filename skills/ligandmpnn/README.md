# LigandMPNN Skill

Inverse folding when the design surface carries a ligand, a nucleic acid or a metal. LigandMPNN puts those atoms into the graph, so the network sees what vanilla ProteinMPNN ignores. Its `run.py` also threads the designed sequence back onto the input coordinates and writes the PDBs next to the FASTA, which is why it is the more convenient runner even for protein-only jobs — it accepts the ProteinMPNN and soluble weights too. The recipe points at the external LigandMPNN repository and its checkpoints; no executable and no weights are bundled here.

LigandMPNN designs around the ligand coordinates it was handed, and it treats that pose as fact. Hand it a docked or modelled placement that is off and you get a pocket shaped to a ligand position that may not be the real one — and the run looks exactly as healthy as a correct one. The `ligand_confidence` in each header is the model scoring the sequence it just wrote, not a measurement of binding. Chain, fixed-residue, context-atom and model-type choices all have to be checked against the actual input structure, and the sequences and threaded structures that come back are design candidates until the bench says otherwise.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install ligandmpnn --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install ligandmpnn`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/ligandmpnn
python3 -m zipfile -c ligandmpnn.zip ligandmpnn
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/ligandmpnn/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Covers repository setup, the model types and their checkpoints, how the PDB and its ligand context are parsed, which chains and residues get designed or held fixed, sampling, the batch outputs, and the threaded PDBs. It also collects the ligand-aware traps, including the one where a stripped HETATM record leaves the model pocket-blind without complaining, and the validation that comes after. |
