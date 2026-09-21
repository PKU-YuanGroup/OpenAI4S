# `openai4s/judgment/templates/`

[中文说明](README_zh.md)

Question templates for the experimental judgment layer. Each template is
English, versioned, and registered with `register_template` so kernel code can
only name a template id. Thresholds live at the top of the module that uses
them.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Imports template modules so they register on package import. |
| [`skills.py`](skills.py) | `skills.suggest` Skill recommendation: fan-out, bioSkills expand, and fit stages. |
| [`bioskills_areas.json`](bioskills_areas.json) | Generated area index (55 areas, 561 members) consumed by the bioSkills expand stage. Rebuild with `uv run python scripts/build_bioskills_area_index.py`. |
| [`literature.py`](literature.py) | `literature.screen` (passage Nouls) and `literature.claim` (supports / contradicts / insufficient). Quote location and number/unit comparison stay in the literature-review sidecar. |
| [`features.py`](features.py) | `features.custom`: caller-supplied Noul/Score questions for text featurization (`allow_custom`, max 24 questions, 400-character instructions, 8000-character state). |
