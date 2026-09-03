# Figure Style Skill

The progressive-disclosure checklist for scientific figures: correctness and legibility, with optional matplotlib helpers behind it. The rules are keyed to the role an element plays, not to a house look: frame, font and palette stay parameters you choose. The checklist says nothing about whether the data you hand it is scientifically true.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install figure-style --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install figure-style`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/figure-style
python3 -m zipfile -c figure-style.zip figure-style
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/figure-style/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The rules themselves: data fidelity and self-consistency, claim-titles tested against every row, label economy (a floor as well as a ceiling), axes and scales, colour, typography, chart family by data shape, layout and narrative, the anti-pattern list, and the render-then-verify QA loop. Sections 1–3, 8 and 9 are correctness and bind everywhere; 4–7 are guidance a deliberate alternative can override — except for the rules inside them that state a factual or perceptual invariant (centring a diverging map on the semantic zero, CVD-safe colour, leader lines that land on the point they name), which bind like the rest. |
| [`kernel.py`](kernel.py) | Optional sidecar. `apply_figure_style` sets the rcParams once before you plot (role-mapped font-size ladder, outward ticks, frameless legends, 300-dpi save, embedded fonts); `set_frame` and `panel_letter` handle the frame and the panel letter; `focal_palette`, `bar_with_points`, `strip_with_median` and `end_of_line_labels` implement the encodings the rules keep asking for, with `goodness_arrow` and `two_tier_label` for the annotations; `panel_crops` returns each panel's crop box in the saved PNG so you can look at what you actually rendered. |

Matplotlib is optional runtime state and has to be installed in whichever kernel you select. A figure that passes the geometric collision check has only been checked geometrically: it still needs the perceptual pass and a domain reader.
