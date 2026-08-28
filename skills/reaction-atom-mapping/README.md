# Reaction Atom Mapping Skill

A local RXNMapper recipe for atom correspondence and changed-bond extraction on
complete reactions. It explicitly refuses target-only queries.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install reaction-atom-mapping --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install reaction-atom-mapping`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/reaction-atom-mapping
python3 -m zipfile -c reaction-atom-mapping.zip reaction-atom-mapping
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/reaction-atom-mapping/` out. If you already run
OpenAI4S there is nothing to install — the wheel ships every bundled Skill, and
a bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Installation, mapping API, reaction-centre derivation, acceptance checks, output contract, and failure modes. |
| [`README.md`](README.md) | English directory index. |
| [`README_zh.md`](README_zh.md) | Chinese directory index. |
