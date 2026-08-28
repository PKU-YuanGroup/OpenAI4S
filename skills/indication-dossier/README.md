# Indication Dossier Skill

Researching one therapeutic indication and writing it up, with the indication treated as a patient population rather than a disease entity: who these patients are, how many of them there are, what is going wrong biologically, how they are treated today, what regulators have accepted before, and which trials shaped the field. Some populations do not map onto a billable diagnosis at all, and saying so is part of the job, because it changes the regulatory path. This is a research and writing recipe. It gives no medical recommendation, and nothing it produces has been checked against a verified evidence database.

The agent does the retrieval itself, and it has to reach current authoritative sources, cite them precisely, leave uncertainty and disagreement between sources on the page instead of smoothing them over, and obey the anti-fabrication gates the reference phases impose. No sidecar and no live data source ships with this Skill.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install indication-dossier --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install indication-dossier`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/indication-dossier
python3 -m zipfile -c indication-dossier.zip indication-dossier
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/indication-dossier/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Drives the run: five phases, each writing a waypoint file, with an identity check after Phase 1 and a resume path when the workdir already holds waypoints. It also fixes the inputs, the tools the Skill expects (and the fallback when an MCP is not connected), the output layout, and what the synthesis phase may still fetch rather than name as a gap. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Loaded on demand: the cross-phase research standards, one instruction file per phase, the writing style rules, and the JSON schemas for the waypoint files. |
