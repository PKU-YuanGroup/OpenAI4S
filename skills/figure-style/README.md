# Figure Style Skill

The progressive-disclosure checklist for scientific figures: correctness and legibility, with optional matplotlib helpers behind it. The rules are keyed to the role an element plays, not to a house look: frame, font and palette stay parameters you choose. The checklist says nothing about whether the data you hand it is scientifically true.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The rules themselves: data fidelity and self-consistency, claim-titles tested against every row, label economy (a floor as well as a ceiling), axes and scales, colour, typography, chart family by data shape, layout and narrative, the anti-pattern list, and the render-then-verify QA loop. Sections 1–3, 8 and 9 are correctness and bind everywhere; 4–7 are guidance a deliberate alternative can override — except for the rules inside them that state a factual or perceptual invariant (centring a diverging map on the semantic zero, CVD-safe colour, leader lines that land on the point they name), which bind like the rest. |
| [`kernel.py`](kernel.py) | Optional sidecar. `apply_figure_style` sets the rcParams once before you plot (role-mapped font-size ladder, outward ticks, frameless legends, 300-dpi save, embedded fonts); `set_frame` and `panel_letter` handle the frame and the panel letter; `focal_palette`, `bar_with_points`, `strip_with_median` and `end_of_line_labels` implement the encodings the rules keep asking for, with `goodness_arrow` and `two_tier_label` for the annotations; `panel_crops` returns each panel's crop box in the saved PNG so you can look at what you actually rendered. |

Matplotlib is optional runtime state and has to be installed in whichever kernel you select. A figure that passes the geometric collision check has only been checked geometrically: it still needs the perceptual pass and a domain reader.
