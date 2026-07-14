# Figure Style Skill

This progressive-disclosure Skill is a correctness and legibility checklist for scientific figures, supported by optional matplotlib helpers. It intentionally defines role-based rules rather than a fixed house style and cannot verify the scientific truth of caller-supplied data by itself.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Recipe covering data fidelity, claim-title checks, label economy, axes/scales, color, typography, chart choice, layout, anti-patterns, and render-then-inspect QA. |
| [`kernel.py`](kernel.py) | Optional sidecar: `apply_figure_style` sets rcParams; `set_frame`, `panel_letter`, and label helpers standardize presentation; palette/bar/strip/line-label helpers implement common encodings; `panel_crops` returns saved-image crop boxes for visual QA. |

## Direct subdirectories

None.

Matplotlib is optional runtime state and must be installed in the selected kernel. Passing collision checks does not replace perceptual or domain review.
