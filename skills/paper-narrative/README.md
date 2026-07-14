# Paper Narrative Skill

This progressive-disclosure Skill reviews the story told by a manuscript and figure deck: hook, claim arc, panel placement, missing analyses, and removable material. It generates editorial proposals rather than scientific evidence or an acceptance prediction.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Defines when to load the Skill and the abstract/caption-to-brief, whole-deck review, figure-move/missing-panel, and handoff-to-composer workflow. |
| [`kernel.py`](kernel.py) | Optional sidecar: provides rebind-safe SDK access; JSON schemas for paper briefs and narrative reviews; `derive_paper_brief` for extracting pitch/vision/figure claims; and `narrative_review_task` for constructing the handling-editor review prompt. |

## Direct subdirectories

None.

Model-generated missing-panel suggestions identify analyses to consider; they do not count as analyses already run.
