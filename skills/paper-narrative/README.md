# Paper Narrative Skill

The outermost of the three progressive-disclosure figure Skills: it judges the story a manuscript and its figure deck tell, and reshapes it. Input is the work itself, so a handling-editor reviewer comes back with a verdict on the hook, the arc from hook to application, panels sitting in the wrong figure, panels that are missing, and material that should be cut. What it produces is editorial opinion, not scientific evidence and not a prediction of acceptance.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install paper-narrative --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install paper-narrative`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/paper-narrative
python3 -m zipfile -c paper-narrative.zip paper-narrative
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/paper-narrative/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | When to load this (while writing or revising a paper, before `figure-composer`) and the workflow: derive the brief from the abstract and captions, review the whole deck as the handling editor, act on the arc, the figure moves, the missing panels and the kill list, hand each surviving figure's claim to `figure-composer`, then re-review the new deck. |
| [`kernel.py`](kernel.py) | Optional sidecar. `pn_sdk` returns a `host` handle that survives a rebind of the name in the kernel; `paper_brief_schema` and `narrative_review_schema` are the two structured-output schemas; `derive_paper_brief` pulls the pitch, the vision and the per-figure claims out of an abstract plus captions in one tool-forced `host.llm` call; `narrative_review_task` builds the handling editor's prompt over the full deck. |

A model-generated missing-panel suggestion names an analysis worth running. It is not an analysis that has been run.
