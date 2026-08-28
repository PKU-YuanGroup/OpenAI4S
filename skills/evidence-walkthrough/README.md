# evidence-walkthrough

The reference end-to-end pass: fixed database query → local analysis →
versioned artifacts carrying lineage → an evidence package that verifies in a
clean environment.

Use it as the first-run demonstration, as a benchmark case (the inputs are
fixed so two runs are comparable), or whenever a result has to be handed to
someone who was not there when it ran.

Verify an exported package the way a recipient would, with no daemon:

```
openai4s verify-package <session>.openai4s-session.zip
```

A pass means the package is **intact**, not that it is authentic — see
[`openai4s/evidence.py`](../../openai4s/evidence.py) for exactly what
verification does and does not establish.

The recipe itself is in [`SKILL.md`](SKILL.md).

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install evidence-walkthrough --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install evidence-walkthrough`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/evidence-walkthrough
python3 -m zipfile -c evidence-walkthrough.zip evidence-walkthrough
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/evidence-walkthrough/` out. If you already run
OpenAI4S there is nothing to install — the wheel ships every bundled Skill, and
a bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).
