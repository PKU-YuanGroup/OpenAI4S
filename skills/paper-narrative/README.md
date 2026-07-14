# Paper Narrative Skill

The outermost of the three progressive-disclosure figure Skills: it judges the story a manuscript and its figure deck tell, and reshapes it. Input is the work itself, so a handling-editor reviewer comes back with a verdict on the hook, the arc from hook to application, panels sitting in the wrong figure, panels that are missing, and material that should be cut. What it produces is editorial opinion, not scientific evidence and not a prediction of acceptance.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | When to load this (while writing or revising a paper, before `figure-composer`) and the workflow: derive the brief from the abstract and captions, review the whole deck as the handling editor, act on the arc, the figure moves, the missing panels and the kill list, hand each surviving figure's claim to `figure-composer`, then re-review the new deck. |
| [`kernel.py`](kernel.py) | Optional sidecar. `pn_sdk` returns a `host` handle that survives a rebind of the name in the kernel; `paper_brief_schema` and `narrative_review_schema` are the two structured-output schemas; `derive_paper_brief` pulls the pitch, the vision and the per-figure claims out of an abstract plus captions in one tool-forced `host.llm` call; `narrative_review_task` builds the handling editor's prompt over the full deck. |

A model-generated missing-panel suggestion names an analysis worth running. It is not an analysis that has been run.
