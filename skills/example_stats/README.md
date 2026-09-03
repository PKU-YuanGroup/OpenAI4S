# Example Stats Skill

A small progressive-disclosure Skill that demonstrates the `SKILL.md` plus Python-sidecar pattern with dependency-free descriptive statistics. The sidecar is loaded only when the Skill is selected, and it works on numeric sequences the caller passes in.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install example_stats --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install example_stats`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/example_stats
python3 -m zipfile -c example_stats.zip example_stats
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/example_stats/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Import examples and short recipes for summaries, quantiles, z-scores, and Pearson correlation. |
| [`kernel.py`](kernel.py) | Optional sidecar over plain Python number lists: `mean`, sample or population `std`, `median`, an interpolated `quantile`, `zscore`, `correlation`, and a combined `summary`. Each one checks its input and refuses an empty sequence. |

These are ordinary calculations, meant for teaching and general use. They do not choose a statistical design, and they do not make an inference valid.
