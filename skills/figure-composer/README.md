# Figure Composer Skill

The middle tier of the three figure Skills: make one publication-grade multi-panel figure good. `paper-narrative` sits above it and decides which figure to make at all; `figure-style` sits below it and rules on a single plot. You enter with a one-sentence claim and the data refs behind it (or with an existing figure, reverse-engineered), fan one sub-agent out per panel, tile the results, and put the composite through an adversarial review loop. The sidecar writes the plans and tasks and composes panel images that already exist; it does not invent a missing analysis, and nothing here predicts whether a journal will take the figure.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install figure-composer --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install figure-composer`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/figure-composer
python3 -m zipfile -c figure-composer.zip figure-composer
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/figure-composer/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The loop, round by round. A 12-column outline fixes each panel's ask and its label budget before anyone draws; one sub-agent takes each panel, with `figure-style` loaded alongside; the crops get looked at before the expensive review is spent on them. Composite feedback comes back in two tiers, outline revisions above and per-panel violations below, regeneration is capped at three rounds, and the anti-patterns listed at the end are the ones that burn a round without improving the figure. If the derived-outline entry point was used, remember the image was untrusted input: every string in that outline came out of a vision model reading pixels. |
| [`kernel.py`](kernel.py) | Optional sidecar. Defines the outline and review schemas and the grid geometry, builds the `panel_task` and `composite_review_task` prompts, tiles the panel PNGs and stamps their letters with `compose_figure`, hands back a crop box per panel, groups the reviewer's blocker and major fixes by panel, and works out which panels an outline revision forces to regenerate. `derive_outline` goes the other direction: one vision call reads an existing figure and proposes an editable outline. |

The vision review calls and the image tooling depend on what the active Host and kernel environment provide. A derived outline and a review are proposals; read them before you act on them.
