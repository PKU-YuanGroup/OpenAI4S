# Figure Composer Skill

This progressive-disclosure Skill coordinates a multi-panel figure workflow from claim and data references through panel fan-out, composition, and adversarial review. Its sidecar creates plans/tasks and composes existing panel images; it does not fabricate missing analyses or guarantee publication acceptance.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Recipe for a 12-column panel outline, one-agent-per-panel work, mandatory `figure-style`, visual inspection, two-tier composite feedback, bounded regeneration rounds, and anti-patterns. |
| [`kernel.py`](kernel.py) | Optional sidecar: defines outline/review schemas and geometry helpers; builds `panel_task` and `composite_review_task`; tiles/stamps panels with `compose_figure`; exposes crops; groups fixes; applies outline revisions; and uses `derive_outline` to propose an editable outline from an existing image. |

## Direct subdirectories

None.

LLM/vision review calls and image tooling depend on the active Host/kernel environment. Derived outlines and reviews remain proposals requiring inspection.
