# Retrosynthesis Planning Skill

This progressive-disclosure Skill describes a target-SMILES route-search and review workflow around an external AiZynthFinder environment. Its pure-stdlib-first sidecar normalizes/ranks exported routes and renders review artifacts; optional RDKit adds structure depictions and optional Host LLM calls add annotations.

The workflow supports planning and chemist triage, not experimental route validation. Conditions, yields, availability, safety, and LLM annotations are hypotheses until checked against literature, ELN/vendor data, and expert review.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Main recipe for target/config/workdir inputs, AiZynthFinder invocation, route normalization/ranking, molecule lookup, LLM annotations, dashboard/report deliverables, and review limitations. |
| [`kernel.py`](kernel.py) | Optional sidecar: canonicalizes SMILES when RDKit exists; builds safe AiZynth commands; loads/normalizes/ranks route trees; collects molecule roles/query/structure sources; builds/parses Host LLM annotation prompts; renders self-contained route tables/AND-OR trees/knowledge graph/dashboard; and writes a Markdown analyst report. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | Deterministic aspirin-shaped route/annotation fixtures plus generated HTML/report and their rebuild script. |
