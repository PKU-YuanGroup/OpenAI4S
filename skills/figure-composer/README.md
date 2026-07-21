# Figure Composer Skill

The middle tier of the three figure Skills: make one publication-grade multi-panel figure good. `paper-narrative` sits above it and decides which figure to make at all; `figure-style` sits below it and rules on a single plot. You enter with a one-sentence claim and the data refs behind it (or with an existing figure, reverse-engineered), fan one sub-agent out per panel, tile the results, and put the composite through an adversarial review loop. The sidecar writes the plans and tasks and composes panel images that already exist; it does not invent a missing analysis, and nothing here predicts whether a journal will take the figure.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The loop, round by round. A 12-column outline fixes each panel's ask and its label budget before anyone draws; one sub-agent takes each panel, with `figure-style` loaded alongside; the crops get looked at before the expensive review is spent on them. Composite feedback comes back in two tiers, outline revisions above and per-panel violations below, regeneration is capped at three rounds, and the anti-patterns listed at the end are the ones that burn a round without improving the figure. If the derived-outline entry point was used, remember the image was untrusted input: every string in that outline came out of a vision model reading pixels. |
| [`kernel.py`](kernel.py) | Optional sidecar. Defines the outline and review schemas and the grid geometry, builds the `panel_task` and `composite_review_task` prompts, tiles the panel PNGs and stamps their letters with `compose_figure`, hands back a crop box per panel, groups the reviewer's blocker and major fixes by panel, and works out which panels an outline revision forces to regenerate. `derive_outline` goes the other direction: one vision call reads an existing figure and proposes an editable outline. |

The vision review calls and the image tooling depend on what the active Host and kernel environment provide. A derived outline and a review are proposals; read them before you act on them.
